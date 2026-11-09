# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import datetime
from unittest.mock import patch

from odoo import fields

from odoo.addons.pms.tests.common import TestPms

from .. import card_crypto

PASSWORD = "correct horse battery"
VISA = {
    "holder": "Jane Doe",
    "pan": "4242 4242 4242 4242",
    "expiryMonth": 3,
    "expiryYear": datetime.date.today().year + 2,
    "cvv": "012",
}


class PaymentCardCase(TestPms):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # The real scrypt cost makes every password check take a while
        kdf_patch = patch.object(card_crypto, "KDF_N", 2**10)
        kdf_patch.start()
        cls.addClassCleanup(kdf_patch.stop)

        cls.room_type = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [cls.pms_property1.id],
                "name": "Double",
                "default_code": "DBL_CARD",
                "class_id": cls.room_type_class1.id,
                "list_price": 50,
            }
        )
        # Enough rooms for the folios a test makes on the same days
        cls.rooms = cls.env["pms.room"].create(
            [
                {
                    "pms_property_id": cls.pms_property1.id,
                    "name": f"Room {number}",
                    "short_name": f"R{number}",
                    "room_type_id": cls.room_type.id,
                    "capacity": 2,
                }
                for number in range(101, 105)
            ]
        )
        cls.guest = cls.env["res.partner"].create({"name": "Guest"})
        cls.sale_channel = cls.env["pms.sale.channel"].create(
            {"name": "Phone", "channel_type": "direct"}
        )
        view = cls.env.ref("pms_payment_card.group_payment_card_view")
        manage = cls.env.ref("pms_payment_card.group_payment_card_manage")
        cls.receptionist = cls._make_user("receptionist", view | manage)
        cls.viewer = cls._make_user("viewer", view)
        cls.clerk = cls._make_user("clerk", cls.env["res.groups"])
        cls.folio = cls._make_folio()
        cls.vault = cls.env["pms.payment.card.vault"]._create_for_property(
            cls.pms_property1, PASSWORD
        )

    @classmethod
    def _make_user(cls, login, card_groups):
        groups = cls.env.ref("base.group_user") | cls.env.ref("pms.group_pms_user")
        return cls.env["res.users"].create(
            {
                "name": login.title(),
                "login": f"{login}@payment-card.test",
                "company_id": cls.company1.id,
                "company_ids": [(6, 0, cls.company1.ids)],
                "pms_property_ids": [(6, 0, cls.pms_property1.ids)],
                "pms_property_id": cls.pms_property1.id,
                "groups_id": [(6, 0, (groups | card_groups).ids)],
            }
        )

    @classmethod
    def _make_folio(cls, days_ago=0):
        checkin = fields.Date.today() - datetime.timedelta(days=days_ago)
        reservation = cls.env["pms.reservation"].create(
            {
                "pms_property_id": cls.pms_property1.id,
                "checkin": checkin,
                "checkout": checkin + datetime.timedelta(days=2),
                "adults": 2,
                "room_type_id": cls.room_type.id,
                "partner_id": cls.guest.id,
                "sale_channel_origin_id": cls.sale_channel.id,
            }
        )
        return reservation.folio_id

    def cards(self, user=None):
        return self.env["pms.folio.payment.card"].with_user(user or self.receptionist)

    def logs(self, folio=None):
        return self.env["pms.payment.card.access.log"].search(
            [("folio_id", "=", (folio or self.folio).id)]
        )

    def refused(self, error_class, operation):
        """Run ``operation`` expecting ``error_class``, without a savepoint.

        ``assertRaises`` rolls back whatever happened inside it. The API keeps
        it instead, because it answers its errors rather than raising them: a
        wrong password leaves its failed attempt and its log behind. These
        tests have to see the same.
        """
        try:
            operation()
        except error_class as error:
            return error
        self.fail(f"{error_class.__name__} not raised")

    def add_channel_card(self, folio=None, **values):
        return self.env["pms.folio.payment.card"]._sync_channel_card(
            folio or self.folio,
            dict(
                {
                    "provider": "fake",
                    "channel_code": "1700000001",
                    "channel_name": "Booking.com",
                    "external_reference": "4093826715",
                    "available_until": fields.Date.today() + datetime.timedelta(days=9),
                },
                **values,
            ),
        )
