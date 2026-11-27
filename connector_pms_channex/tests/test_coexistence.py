# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""Two channel managers in the same instance and in the same hotel.

This is the requirement the whole generic layer exists for, and it cannot be
checked from one connector alone. It lives here rather than in
``connector_pms`` because it needs two concrete connectors installed, and
Channex is the second one to arrive; it skips when Wubook is absent.

Between them the two connectors cover the fallback in both directions, which is
why no third, fake connector is needed: Channex overrides ``connect.candidates``
and inherits the generic ``connect.hooks``, and Wubook does the opposite.
"""

import datetime

from odoo.tests.common import tagged
from odoo.tools import mute_logger

from .common import ChannexConnectorCase
from .test_ari_availability import ChannexAvailabilityCase

WUBOOK_INSTALLED = True
try:
    from odoo.addons.connector_pms_wubook.tests.test_master_sync import (  # noqa: F401
        _make_backend_environment,
    )
except ImportError:  # pragma: no cover
    WUBOOK_INSTALLED = False

CHANNEX_ROOM_TYPE_BINDING = "channel.channex.pms.room.type"
WUBOOK_ROOM_TYPE_BINDING = "channel.wubook.pms.room.type"
WUBOOK_PROPERTY_AVAILABILITY_BINDING = "channel.wubook.pms.property.availability"


def _make_wubook_backend(env, pms_property, name):
    backend_type = env["channel.wubook.backend.type"].create(
        {
            "name": f"BT {name}",
            "model_type_id": env.ref(
                "connector_pms_wubook.model_channel_wubook_backend_type"
            ).id,
            "direct_channel_type_id": env.ref(
                "connector_pms_wubook.main_pms_sale_channel_wubook"
            ).id,
        }
    )
    payment_method_line = env["account.payment.method.line"].search([], limit=1)
    return env["channel.wubook.backend"].create(
        {
            "name": name,
            "pms_property_id": pms_property.id,
            "backend_type_id": backend_type.parent_id.id,
            "username": "X",
            "password": "X",
            "property_code": "X",
            "pkey": "X",
            "pricelist_external_id": 1,
            "wubook_payment_method_line_id": payment_method_line.id,
        }
    )


@tagged("post_install", "-at_install")
class TestTwoChannelManagers(ChannexConnectorCase):
    """A Channex backend and a Wubook backend on the same property."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not WUBOOK_INSTALLED:
            return
        cls.wubook_backend = cls._make_wubook_backend(cls.pms_property, "Wubook Coex")
        cls.other_property = cls.env["pms.property"].create(
            {
                "name": "Coexistence Other Property",
                "company_id": cls.company.id,
                "default_pricelist_id": cls.pricelist.id,
            }
        )
        cls.other_wubook_backend = cls._make_wubook_backend(
            cls.other_property, "Wubook Other Hotel"
        )

    @classmethod
    def _make_wubook_backend(cls, pms_property, name):
        return _make_wubook_backend(cls.env, pms_property, name)

    def setUp(self):
        super().setUp()
        if not WUBOOK_INSTALLED:
            self.skipTest("connector_pms_wubook is not installed")

    def _bind_channex(self, record, external_id):
        return (
            self.env[CHANNEX_ROOM_TYPE_BINDING]
            .with_context(connector_no_export=True)
            .create(
                {
                    "odoo_id": record.id,
                    "backend_id": self.backend.id,
                    "external_id": external_id,
                }
            )
        )

    def _bind_wubook(self, record, external_id):
        return (
            self.env[WUBOOK_ROOM_TYPE_BINDING]
            .with_context(connector_no_export=True)
            .create(
                {
                    "odoo_id": record.id,
                    "backend_id": self.wubook_backend.id,
                    "external_id": external_id,
                }
            )
        )

    # -- registry ----------------------------------------------------------

    def test_registry_discovers_both_connectors(self):
        fields_ = self.env["channel.backend"]._channel_binding_field_names(
            "pms.room.type"
        )
        self.assertIn("channel_channex_bind_ids", fields_)
        self.assertIn("channel_wubook_bind_ids", fields_)

    def test_binding_model_resolved_per_connector(self):
        self.assertEqual(
            self.backend.parent_id._channel_binding_model("pms.room.type"),
            CHANNEX_ROOM_TYPE_BINDING,
        )
        self.assertEqual(
            self.wubook_backend.parent_id._channel_binding_model("pms.room.type"),
            WUBOOK_ROOM_TYPE_BINDING,
        )

    # -- component resolution ----------------------------------------------

    def test_each_backend_resolves_its_own_adapter(self):
        with self.backend.work_on(CHANNEX_ROOM_TYPE_BINDING) as work:
            channex_adapter = work.component(usage="backend.adapter")
        with self.wubook_backend.work_on(WUBOOK_ROOM_TYPE_BINDING) as work:
            wubook_adapter = work.component(usage="backend.adapter")
        self.assertNotEqual(channex_adapter._name, wubook_adapter._name)
        self.assertEqual(channex_adapter._name, "channel.channex.pms.room.type.adapter")

    def test_vendor_component_wins_where_it_is_declared(self):
        """Both usages are overridden by exactly one of the two connectors."""
        with self.backend.work_on(CHANNEX_ROOM_TYPE_BINDING) as work:
            self.assertEqual(
                work.component(usage="connect.candidates")._name,
                "channel.channex.connect.candidates",
            )
        with self.wubook_backend.work_on(WUBOOK_ROOM_TYPE_BINDING) as work:
            self.assertEqual(
                work.component(usage="connect.hooks")._name,
                "channel.wubook.connect.hooks",
            )

    def test_generic_component_serves_the_connector_that_does_not_override_it(self):
        """The generic components carry no ``_collection``, so they have to
        reach a connector that never declared one of their usage. Checked in
        both directions: Channex has no hooks of its own, Wubook no candidates.
        """
        with self.backend.work_on(CHANNEX_ROOM_TYPE_BINDING) as work:
            self.assertEqual(
                work.component(usage="connect.hooks")._name, "channel.connect.hooks"
            )
        with self.wubook_backend.work_on(WUBOOK_ROOM_TYPE_BINDING) as work:
            self.assertEqual(
                work.component(usage="connect.candidates")._name,
                "channel.connect.candidates",
            )

    # -- bindings in the same hotel ----------------------------------------

    def test_same_hotel_two_connectors_keep_separate_bindings(self):
        """The unique constraint is per (backend, record), so the same room type
        can be bound once per channel manager in the same hotel."""
        channex_binding = self._bind_channex(self.room_type, "rt-channex")
        wubook_binding = self._bind_wubook(self.room_type, 4242)
        self.assertEqual(channex_binding.odoo_id, wubook_binding.odoo_id)
        self.assertNotEqual(channex_binding._name, wubook_binding._name)
        self.assertEqual(
            channex_binding.backend_id.pms_property_id,
            wubook_binding.backend_id.pms_property_id,
        )

    def test_bindings_of_both_connectors_are_listed(self):
        self._bind_channex(self.room_type, "rt-channex")
        self._bind_wubook(self.room_type, 4242)
        bindings = self.room_type._channel_bindings()
        # One recordset per connector: they are different models and cannot be
        # merged into a single one.
        self.assertEqual(len(bindings), 2)
        self.assertEqual(
            {recordset._name for recordset in bindings},
            {CHANNEX_ROOM_TYPE_BINDING, WUBOOK_ROOM_TYPE_BINDING},
        )

    def test_connection_state_and_backends_cover_every_connector(self):
        self._bind_channex(self.room_type, "rt-channex")
        self.room_type.invalidate_recordset()
        self.assertEqual(self.room_type.channel_connection_state, "connected")
        self.assertEqual(self.room_type.channel_backend_ids, self.backend.parent_id)

        self._bind_wubook(self.room_type, 4242)
        self.room_type.invalidate_recordset()
        self.assertEqual(
            self.room_type.channel_backend_ids,
            self.backend.parent_id | self.wubook_backend.parent_id,
        )

    # -- wizard ------------------------------------------------------------

    def test_wizard_offers_every_connector_of_the_property(self):
        self.assertEqual(
            self.room_type._channel_candidate_backends(),
            self.backend.parent_id | self.wubook_backend.parent_id,
        )

    def test_wizard_never_offers_another_hotels_backend(self):
        self.assertNotIn(
            self.other_wubook_backend.parent_id,
            self.room_type._channel_candidate_backends(),
        )

    @mute_logger("odoo.addons.connector_pms.wizards.wizard_connect")
    def test_wizard_binds_through_the_chosen_connector(self):
        wizard = self.env["channel.connect.wizard"].create(
            {
                "res_model": "pms.room.type",
                "res_id": self.room_type.id,
                "backend_id": self.backend.parent_id.id,
                "mode": "manual",
                "manual_external_id": "rt-manual",
            }
        )
        self.assertEqual(wizard.binding_model, CHANNEX_ROOM_TYPE_BINDING)
        wizard.action_connect()
        binding = self.env[CHANNEX_ROOM_TYPE_BINDING].search(
            [("odoo_id", "=", self.room_type.id)]
        )
        self.assertEqual(len(binding), 1)
        # Channex external ids are UUIDs, so the Char flavour survives as text;
        # Wubook's Integer one is cast on the way in.
        self.assertEqual(binding.external_id, "rt-manual")
        self.assertFalse(
            self.env[WUBOOK_ROOM_TYPE_BINDING].search(
                [("odoo_id", "=", self.room_type.id)]
            )
        )


@tagged("post_install", "-at_install")
class TestAvailabilityAcrossChannelManagers(ChannexAvailabilityCase):
    """A booking one channel manager brings in takes the room off the others.

    Each import writes with every listener silenced, those of the other
    connector included, so this only holds if the importer publishes on every
    channel manager of the property and not only on its own.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not WUBOOK_INSTALLED:
            return
        cls.wubook_backend = _make_wubook_backend(
            cls.env, cls.pms_property, "Wubook Across"
        )
        quiet = cls.env(context=dict(cls.env.context, connector_no_export=True))
        quiet[WUBOOK_ROOM_TYPE_BINDING].create(
            {
                "odoo_id": cls.room_type.id,
                "backend_id": cls.wubook_backend.id,
                "external_id": 4242,
            }
        )
        cls.wubook_availability = quiet[WUBOOK_PROPERTY_AVAILABILITY_BINDING].create(
            {
                "odoo_id": cls.pms_property.id,
                "backend_id": cls.wubook_backend.id,
                "external_id": cls.pms_property.id,
            }
        )

    def setUp(self):
        if not WUBOOK_INSTALLED:
            self.skipTest("connector_pms_wubook is not installed")
        super().setUp()

    def test_a_booking_from_channex_reaches_wubook(self):
        checkin = self.first + datetime.timedelta(days=20)
        checkout = checkin + datetime.timedelta(days=2)
        self._seed_booking(
            arrival_date=str(checkin),
            departure_date=str(checkout),
            rooms=[
                self._room(
                    checkin_date=str(checkin),
                    checkout_date=str(checkout),
                    days={
                        str(checkin): "76.50",
                        str(checkin + datetime.timedelta(days=1)): "76.50",
                    },
                )
            ],
        )
        self._import()
        trap = self._commit()
        trap.assert_enqueued_job(
            self.wubook_availability.export_record,
            args=(self.wubook_backend, self.pms_property),
            properties={
                "identity_key": (
                    f"wubook_export_property_avail:{self.wubook_backend.id}"
                    f":{self.pms_property.id}"
                )
            },
        )
        self.assertEqual(
            (
                self.wubook_availability.wubook_pending_date_from,
                self.wubook_availability.wubook_pending_date_to,
            ),
            (checkin, checkin + datetime.timedelta(days=1)),
        )

    def test_a_booking_from_wubook_reaches_channex(self):
        quiet = self.env(context=dict(self.env.context, connector_no_export=True))
        reservation = quiet["pms.reservation"].create(
            {
                "pms_property_id": self.pms_property.id,
                "room_type_id": self.room_type.id,
                "preferred_room_id": self.rooms[0].id,
                "checkin": self.night,
                "checkout": self.night + datetime.timedelta(days=2),
                "partner_name": "A guest",
                "adults": 1,
                "sale_channel_origin_id": self.door.id,
            }
        )
        # Whatever a deferred recompute may have noted goes out now, so what
        # is pending below is what the import noted.
        self._publish()
        self.assertEqual(self._pending(), [])
        folio_binding = quiet["channel.wubook.pms.folio"].create(
            {
                "odoo_id": reservation.folio_id.id,
                "backend_id": self.wubook_backend.id,
                "external_id": 987654,
            }
        )
        with self.wubook_backend.work_on("channel.wubook.pms.folio") as work:
            importer = work.component_by_name("channel.wubook.pms.folio.importer")
            importer._after_import(folio_binding)
        self._commit()
        self.assertEqual(
            self._pending(), [(self.night, self.night + datetime.timedelta(days=1))]
        )
