import datetime

from odoo import fields
from odoo.tests import tagged

from odoo.addons.pms.tests.common import TestPms


@tagged("post_install", "-at_install")
class TestRoomTypeDailySummary(TestPms):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tax_included = cls._create_tax("Room VAT 10% included", True)
        cls.tax_excluded = cls._create_tax("Room VAT 10% excluded", False)
        cls.room_type = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [cls.pms_property1.id],
                "name": "Double Summary",
                "default_code": "DBL_SUM",
                "class_id": cls.room_type_class1.id,
                "list_price": 110,
            }
        )
        cls.room_type.product_id.taxes_id = cls.tax_included
        cls.rooms = cls.env["pms.room"].create(
            [
                {
                    "pms_property_id": cls.pms_property1.id,
                    "name": f"Summary {number}",
                    "short_name": f"SM{number}",
                    "room_type_id": cls.room_type.id,
                    "capacity": 2,
                }
                for number in range(1, 5)
            ]
        )
        cls.partner = cls.env["res.partner"].create({"name": "Summary Guest"})
        cls.sale_channel = cls.env["pms.sale.channel"].create(
            {"name": "Direct Summary", "channel_type": "direct"}
        )
        cls.closure_reason = cls.env["room.closure.reason"].create(
            {"name": "Maintenance", "description": "Room under maintenance"}
        )
        cls.day = fields.Date.today()

    @classmethod
    def _create_tax(cls, name, price_include):
        return cls.env["account.tax"].create(
            {
                "name": name,
                "amount": 10,
                "amount_type": "percent",
                "type_tax_use": "sale",
                "price_include": price_include,
                "company_id": cls.company1.id,
                "country_id": cls.env.ref("base.es").id,
            }
        )

    def _create_reservation(self, room, reservation_type="normal"):
        vals = {
            "pms_property_id": self.pms_property1.id,
            "checkin": self.day,
            "checkout": self.day + datetime.timedelta(days=1),
            "adults": 1,
            "room_type_id": self.room_type.id,
            "preferred_room_id": room.id,
            "partner_id": self.partner.id,
            "sale_channel_origin_id": self.sale_channel.id,
            "reservation_type": reservation_type,
        }
        if reservation_type == "out":
            vals["closure_reason_id"] = self.closure_reason.id
        return self.env["pms.reservation"].create(vals)

    def _summary(self):
        self.env.flush_all()
        groups = self.env["hotel.room.type.daily.summary"].read_group(
            [("pms_property_id", "=", self.pms_property1.id), ("day", "=", self.day)],
            ["rooms_sold", "total_revenue", "number_of_rooms", "adr", "occupancy"],
            ["pms_property_id"],
        )
        self.assertEqual(len(groups), 1)
        return groups[0]

    def test_out_of_service_and_staff_are_not_sold(self):
        """Blocked and staff rooms are neither sold rooms nor revenue, so they
        must not raise the occupancy nor drag the ADR down."""
        self._create_reservation(self.rooms[0])
        self._create_reservation(self.rooms[1], "out")
        self._create_reservation(self.rooms[2], "staff")
        summary = self._summary()
        self.assertEqual(summary["number_of_rooms"], 4)
        self.assertEqual(summary["rooms_sold"], 1)
        self.assertAlmostEqual(summary["occupancy"], 25.0)
        self.assertAlmostEqual(summary["adr"], 100.0)

    def test_revenue_excludes_price_included_tax(self):
        reservation = self._create_reservation(self.rooms[0])
        self.assertAlmostEqual(reservation.price_total, 110.0)
        self.assertAlmostEqual(self._summary()["total_revenue"], 100.0)

    def test_revenue_keeps_price_when_tax_is_excluded(self):
        self.room_type.product_id.taxes_id = self.tax_excluded
        reservation = self._create_reservation(self.rooms[0])
        self.assertAlmostEqual(reservation.price_subtotal, 110.0)
        self.assertAlmostEqual(self._summary()["total_revenue"], 110.0)

    def test_revenue_applies_line_discount(self):
        reservation = self._create_reservation(self.rooms[0])
        reservation.reservation_line_ids.discount = 50
        self.assertAlmostEqual(self._summary()["total_revenue"], 50.0)

    def test_cancelled_reservation_is_not_sold(self):
        reservation = self._create_reservation(self.rooms[0])
        reservation.action_cancel()
        summary = self._summary()
        self.assertEqual(summary["rooms_sold"], 0)
        self.assertAlmostEqual(summary["total_revenue"], 0.0)
