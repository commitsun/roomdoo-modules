from datetime import date, timedelta

from freezegun import freeze_time

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestPmsDiscountsSummary(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        plan = cls.env["pms.availability.plan"].create({"name": "AI plan"})
        pricelist = cls.env["product.pricelist"].create(
            {
                "name": "AI pricelist",
                "availability_plan_id": plan.id,
                "is_pms_available": True,
            }
        )
        cls.prop1, cls.prop2, cls.prop3 = cls.env["pms.property"].create(
            [
                {
                    "name": f"AI property {n}",
                    "company_id": cls.env.company.id,
                    "default_pricelist_id": pricelist.id,
                }
                for n in (1, 2, 3)
            ]
        )
        room_class = cls.env["pms.room.type.class"].create(
            {"name": "AI class", "default_code": "AIC"}
        )
        room_type = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [(6, 0, (cls.prop1 | cls.prop2 | cls.prop3).ids)],
                "name": "AI double",
                "default_code": "AID",
                "class_id": room_class.id,
            }
        )
        cls.env["pms.room"].create(
            [
                {
                    "pms_property_id": prop.id,
                    "name": f"Room {n}",
                    "short_name": f"R{n}",
                    "room_type_id": room_type.id,
                    "capacity": 2,
                }
                for prop in (cls.prop1, cls.prop2, cls.prop3)
                for n in range(4)
            ]
        )
        cls.direct = cls.env["pms.sale.channel"].create(
            {"name": "AI door", "channel_type": "direct"}
        )
        cls.ota = cls.env["pms.sale.channel"].create(
            {"name": "AI OTA", "channel_type": "indirect"}
        )
        cls.partner = cls.env["res.partner"].create({"name": "AI guest"})
        cls.user = new_test_user(
            cls.env,
            login="ai_discounts",
            groups="base.group_user,pms.group_pms_user",
            pms_property_ids=[(6, 0, (cls.prop1 | cls.prop2).ids)],
            pms_property_id=cls.prop1.id,
        )
        cls.room_type = room_type
        with freeze_time("2026-09-01"):
            # Current week: 2026-09-21 to 2026-09-27
            cls._reservation(cls.prop1, cls.direct, [(21, 100, 0), (22, 100, 20)])
            cls._reservation(cls.prop1, cls.direct, [(23, 100, 100)])
            cls._reservation(cls.prop1, cls.ota, [(24, 0, 0), (25, 80, 50)])
            cls._reservation(cls.prop1, cls.direct, [(21, 100, 30)], type="staff")
            cancelled = cls._reservation(cls.prop1, cls.direct, [(26, 100, 40)])
            cancelled.action_cancel()
            cls._reservation(cls.prop2, cls.direct, [(22, 200, 10)])
            cls._reservation(cls.prop3, cls.direct, [(22, 200, 90)])
            # Previous week, no discounts
            cls._reservation(cls.prop1, cls.direct, [(14, 100, 0), (15, 100, 0)])
        # The PMS reprices nights stored at 0 whenever it recomputes them, so
        # set the free night as it is found in real data.
        cls.env.flush_all()
        cls.env.cr.execute(
            """UPDATE pms_reservation_line SET price = 0, price_day_total = 0
                WHERE pms_property_id = %s AND date = '2026-09-24'""",
            [cls.prop1.id],
        )
        cls.env.invalidate_all()
        cls.tool = cls.env.ref("pms_ai_tool.ai_tool_pms_discounts_summary")

    @classmethod
    def _reservation(cls, prop, channel, nights, type="normal"):
        """Create a reservation with (day of September, price, discount) nights."""
        reservation = cls.env["pms.reservation"].create(
            {
                "pms_property_id": prop.id,
                "room_type_id": cls.room_type.id,
                "partner_id": cls.partner.id,
                "sale_channel_origin_id": channel.id,
                "reservation_type": type,
                "reservation_line_ids": [
                    (0, 0, {"date": date(2026, 9, day)}) for day, _p, _d in nights
                ],
            }
        )
        for line, (_day, price, discount) in zip(
            reservation.reservation_line_ids.sorted("date"), nights, strict=True
        ):
            line.write(
                {"price": price, "discount": discount, "sale_channel_id": channel.id}
            )
        return reservation

    def _run(self, **kwargs):
        return self.tool.with_user(self.user)._execute_tool(**kwargs)

    def test_week_summary(self):
        response = self._run(date_from="2026-09-21", date_to="2026-09-27")
        result = response["result"]
        self.assertEqual(
            result["totals"],
            {
                "nights": 6,
                "discounted_nights": 4,
                "discounted_nights_pct": 66.7,
                "nights_50_or_more": 2,
                "nights_100": 1,
                "discount_amount": 180.0,
                "discount_amount_100": 100.0,
                "avg_discount_pct": 45.0,
                "properties_with_discount": 2,
            },
        )
        channels = {row["channel"]: row for row in result["by_channel"]}
        self.assertEqual(set(channels), {"AI door", "AI OTA"})
        self.assertEqual(channels["AI door"]["nights"], 4)
        self.assertEqual(channels["AI door"]["discount_amount"], 140.0)
        self.assertEqual(channels["AI door"]["channel_type"], "direct")
        self.assertEqual(channels["AI OTA"]["discounted_nights"], 1)
        self.assertEqual(channels["AI OTA"]["discount_amount"], 40.0)

        prop1, prop2 = result["by_property"]
        self.assertEqual(prop1["property_id"], self.prop1.id)
        self.assertEqual(prop1["nights"], 5)
        self.assertEqual(prop1["discounted_nights_pct"], 60.0)
        self.assertEqual(prop1["history_periods"], 1)
        self.assertEqual(prop1["history_discounted_nights_pct"], 0.0)
        self.assertEqual(prop1["change_pct_points"], 60.0)
        self.assertEqual(prop2["property_id"], self.prop2.id)
        self.assertEqual(prop2["discount_amount"], 20.0)
        self.assertIsNone(prop2["history_discounted_nights_pct"])
        self.assertEqual(
            [row["property_id"] for row in result["rising"]], [self.prop1.id]
        )

        self.assertEqual(response["period"]["days"], 7)
        self.assertEqual(
            response["period"]["history"][0],
            {"date_from": "2026-09-14", "date_to": "2026-09-20"},
        )
        self.assertIn("1 nights have a price of 0", response["warnings"][0])
        self.assertTrue(response["definitions"])

    def test_property_filter_and_top(self):
        result = self._run(
            date_from="2026-09-21", date_to="2026-09-27", property_ids=[self.prop2.id]
        )["result"]
        self.assertEqual(result["totals"]["nights"], 1)
        result = self._run(date_from="2026-09-21", date_to="2026-09-27", top=1)
        self.assertEqual(len(result["result"]["by_property"]), 1)
        self.assertIn("Only the first 1 of 2", result["warnings"][-1])

    def test_scope_and_limits(self):
        with self.assertRaises(AccessError):
            self._run(
                date_from="2026-09-21",
                date_to="2026-09-27",
                property_ids=[self.prop3.id],
            )
        start = date(2026, 9, 1)
        with self.assertRaises(UserError):
            self._run(
                date_from=start.isoformat(),
                date_to=(start + timedelta(days=31)).isoformat(),
            )
