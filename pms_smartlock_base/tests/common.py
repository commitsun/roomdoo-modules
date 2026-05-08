from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase


class CommonSmartlock(TransactionCase):
    """Base setup for smartlock tests.

    Reuses the first ``pms.property`` from the database and builds a
    minimal test scaffold under it: a vendor, two smartlock-enabled
    rooms, one lock-less room, a sale channel, a partner, and a room
    type. Everything happens inside the test's transaction and is
    rolled back at the end.

    The ``test`` ``vendor_type`` is registered at class setup so the
    base module's tests don't depend on a vendor-specific module
    being installed.

    The vendor's ``get_connector`` is **not** patched here. Tests that
    need to verify vendor calls should mock it themselves; tests that
    just verify Odoo-side logic should use ``trap_jobs()`` so the sync
    methods never run.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(user=cls.env.ref("base.user_admin"))
        # Tests create lock.vendor / lock.code records directly. Both
        # models are restricted to ``group_smartlock_admin``; grant it to
        # the test user so the existing creation flow keeps working
        # without spraying ``sudo()`` across the test scaffold.
        cls.env.user.write(
            {
                "groups_id": [
                    (4, cls.env.ref("pms_smartlock_base.group_smartlock_admin").id)
                ]
            }
        )

        cls._register_test_vendor_type()

        cls.pms_property = cls.env["pms.property"].search([], limit=1)
        if not cls.pms_property:
            raise RuntimeError("No pms.property in DB; tests need at least one")

        existing_class = cls.env["pms.room.type.class"].search([], limit=1)
        if existing_class:
            cls.room_type_class = existing_class
        else:
            cls.room_type_class = cls.env["pms.room.type.class"].create(
                {"name": "Smartlock Test Class", "default_code": "SLTC"}
            )

        cls.room_type = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [cls.pms_property.id],
                "name": "Smartlock Test Type",
                "default_code": "SLT_%s" % cls.env.cr.now().strftime("%H%M%S%f"),
                "class_id": cls.room_type_class.id,
                "list_price": 50,
            }
        )
        cls.sale_channel = cls.env["pms.sale.channel"].search(
            [("channel_type", "=", "direct")], limit=1
        ) or cls.env["pms.sale.channel"].create(
            {"name": "Direct Test", "channel_type": "direct"}
        )
        cls.partner = cls.env["res.partner"].create({"name": "Smartlock Test Guest"})
        cls.closure_reason = cls.env["room.closure.reason"].create(
            {"name": "Smartlock Test Closure"}
        )

        cls.vendor = cls.env["lock.vendor"].create(
            {
                "name": "Test Vendor",
                "vendor_type": "test",
                "pms_property_id": cls.pms_property.id,
            }
        )
        cls.room_a = cls.env["pms.room"].create(
            {
                "pms_property_id": cls.pms_property.id,
                "name": "SL-A",
                "room_type_id": cls.room_type.id,
                "capacity": 2,
                "lock_vendor_id": cls.vendor.id,
                "lock_device_id": "device-A",
            }
        )
        cls.room_b = cls.env["pms.room"].create(
            {
                "pms_property_id": cls.pms_property.id,
                "name": "SL-B",
                "room_type_id": cls.room_type.id,
                "capacity": 2,
                "lock_vendor_id": cls.vendor.id,
                "lock_device_id": "device-B",
            }
        )
        cls.room_no_lock = cls.env["pms.room"].create(
            {
                "pms_property_id": cls.pms_property.id,
                "name": "SL-NoLock",
                "room_type_id": cls.room_type.id,
                "capacity": 2,
            }
        )

    @classmethod
    def _register_test_vendor_type(cls):
        """Add a 'test' option to ``lock.vendor.vendor_type`` so we can
        create vendor records without installing a vendor-specific module.
        Idempotent across test classes in the same process."""
        field = cls.env["lock.vendor"]._fields["vendor_type"]
        current = list(field.selection or [])
        if "test" not in (value for value, _ in current):
            field.selection = current + [("test", "Test")]

    def _create_reservation(self, **overrides):
        """Build a confirmed reservation in ``room_a`` 30 days out — far
        enough that ``_should_have_lock_codes`` is False unless a live
        ``lock.code`` is planted, which keeps the predicate's two
        gating branches independently testable."""
        today = fields.Date.context_today(self.env.user)
        vals = {
            "pms_property_id": self.pms_property.id,
            "checkin": today + timedelta(days=30),
            "checkout": today + timedelta(days=33),
            "adults": 1,
            "partner_id": self.partner.id,
            "preferred_room_id": self.room_a.id,
            "room_type_id": self.room_type.id,
            "sale_channel_origin_id": self.sale_channel.id,
        }
        vals.update(overrides)
        return self.env["pms.reservation"].create(vals)

    def _plant_live_code(self, reservation, room=None, **overrides):
        """Create a ``lock.code`` already synced to the (fake) vendor so
        ``_should_have_lock_codes`` returns True (system is committed)
        regardless of the horizon."""
        room = room or reservation.preferred_room_id
        vals = {
            "reservation_id": reservation.id,
            "room_id": room.id,
            "vendor_id": room.lock_vendor_id.id,
            "date_from": reservation.checkin_datetime,
            "date_to": reservation.checkout_datetime,
            "vendor_code_id": "vendor-code-1",
            "pin": "1234",
        }
        vals.update(overrides)
        return self.env["lock.code"].sudo().create(vals)
