from datetime import date

from freezegun import freeze_time

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, new_test_user, tagged

from ..models.pms_service_nights import PARAM_PRODUCTS


@tagged("post_install", "-at_install")
class TestPmsServiceNights(TransactionCase):
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
        cls.prop = cls.env["pms.property"].create(
            {
                "name": "AI eco property",
                "company_id": cls.env.company.id,
                "default_pricelist_id": pricelist.id,
            }
        )
        room_class = cls.env["pms.room.type.class"].create(
            {"name": "AI class", "default_code": "AIE"}
        )
        cls.room_type = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [(6, 0, cls.prop.ids)],
                "name": "AI double",
                "default_code": "AIED",
                "class_id": room_class.id,
            }
        )
        cls.env["pms.room"].create(
            [
                {
                    "pms_property_id": cls.prop.id,
                    "name": f"Room {n}",
                    "short_name": f"E{n}",
                    "room_type_id": cls.room_type.id,
                    "capacity": 2,
                }
                for n in range(4)
            ]
        )
        cls.channel = cls.env["pms.sale.channel"].create(
            {"name": "AI door", "channel_type": "direct"}
        )
        cls.partner = cls.env["res.partner"].create({"name": "AI guest"})
        Product = cls.env["product.product"]
        cls.after = Product.create(
            {
                "name": "AI eco after",
                "type": "service",
                "per_day": True,
                "consumed_on": "after",
            }
        )
        cls.before = Product.create(
            {
                "name": "AI eco before",
                "type": "service",
                "per_day": True,
                "consumed_on": "before",
            }
        )
        cls.user = new_test_user(
            cls.env,
            login="ai_eco",
            groups="base.group_user,pms.group_pms_user",
            pms_property_ids=[(6, 0, cls.prop.ids)],
            pms_property_id=cls.prop.id,
        )
        with freeze_time("2026-09-01"):
            cls._stay(21, 26, cls.after, [21, 23, 26])
            cls._stay(21, 22, cls.after, [22])
            cancelled = cls._stay(21, 24, cls.after, [23])
            cancelled.action_cancel()
            cls._stay(21, 24, cls.before, [21, 23, 24])
        cls.tool = cls.env.ref("pms_ai_tool.ai_tool_pms_service_nights")

    @classmethod
    def _stay(cls, checkin, checkout, product, days):
        reservation = cls.env["pms.reservation"].create(
            {
                "pms_property_id": cls.prop.id,
                "room_type_id": cls.room_type.id,
                "partner_id": cls.partner.id,
                "sale_channel_origin_id": cls.channel.id,
                "checkin": date(2026, 9, checkin),
                "checkout": date(2026, 9, checkout),
            }
        )
        cls.env["pms.service"].create(
            {
                "reservation_id": reservation.id,
                "product_id": product.id,
                "no_auto_add_lines": True,
                "service_line_ids": [
                    (0, 0, {"date": date(2026, 9, day), "day_qty": 1}) for day in days
                ],
            }
        )
        return reservation

    def _run(self, **kwargs):
        return self.tool.with_user(self.user)._execute_tool(**kwargs)

    def test_rules(self):
        response = self._run(
            date_from="2026-09-21",
            date_to="2026-09-27",
            product_ids=[self.after.id, self.before.id],
        )
        self.assertEqual(
            response["result"]["totals"],
            {
                "nights": 2,
                "cancelled": 1,
                "one_night_stays": 1,
                "last_night": 2,
                "outside_stay": 2,
            },
        )
        (row,) = response["result"]["by_property"]
        self.assertEqual(row["property_id"], self.prop.id)
        self.assertEqual(row["nights"], 2)
        self.assertIn("outside the stay", response["warnings"][0])

    def test_only_after_product_and_period(self):
        totals = self._run(
            date_from="2026-09-22", date_to="2026-09-25", product_ids=[self.after.id]
        )["result"]["totals"]
        # Lines on the 21st and the 26th fall outside the period.
        self.assertEqual(totals["nights"], 1)
        self.assertEqual(totals["last_night"], 0)
        self.assertEqual(totals["outside_stay"], 0)
        self.assertEqual(totals["one_night_stays"], 1)

    def test_configured_products(self):
        params = self.env["ir.config_parameter"].sudo()
        params.set_param(PARAM_PRODUCTS, str(self.before.id))
        totals = self._run(date_from="2026-09-21", date_to="2026-09-27")["result"][
            "totals"
        ]
        self.assertEqual(totals["nights"], 1)
        params.set_param(PARAM_PRODUCTS, "")
        with self.assertRaisesRegex(UserError, "No product"):
            self._run(date_from="2026-09-21", date_to="2026-09-27")
        with self.assertRaisesRegex(UserError, "Unknown products"):
            self._run(
                date_from="2026-09-21", date_to="2026-09-27", product_ids=[999999]
            )
