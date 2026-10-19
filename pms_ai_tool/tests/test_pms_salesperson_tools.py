from datetime import date

from freezegun import freeze_time

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, new_test_user, tagged

from ..models.pms_salesperson_tools import PARAM_SALESPEOPLE


@tagged("post_install", "-at_install")
class TestPmsSalespersonTools(TransactionCase):
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
        # As the pms tests do: every pricelist usable by the PMS (agencies get
        # the default one).
        for other in cls.env["product.pricelist"].search([]):
            if not other.availability_plan_id:
                other.write({"availability_plan_id": plan.id, "is_pms_available": True})
        cls.prop = cls.env["pms.property"].create(
            {
                "name": "AI sales property",
                "company_id": cls.env.company.id,
                "default_pricelist_id": pricelist.id,
            }
        )
        room_class = cls.env["pms.room.type.class"].create(
            {"name": "AI class", "default_code": "AIS"}
        )
        cls.room_type = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [(6, 0, cls.prop.ids)],
                "name": "AI double",
                "default_code": "AISD",
                "class_id": room_class.id,
            }
        )
        cls.env["pms.room"].create(
            [
                {
                    "pms_property_id": cls.prop.id,
                    "name": f"Room {n}",
                    "short_name": f"S{n}",
                    "room_type_id": cls.room_type.id,
                    "capacity": 2,
                }
                for n in range(6)
            ]
        )
        cls.direct = cls.env["pms.sale.channel"].create(
            {"name": "AI door", "channel_type": "direct"}
        )
        cls.indirect = cls.env["pms.sale.channel"].create(
            {"name": "AI agencies", "channel_type": "indirect"}
        )
        cls.sp_a = new_test_user(cls.env, login="ai_sp_a", name="Salesperson A")
        cls.sp_b = new_test_user(cls.env, login="ai_sp_b", name="Salesperson B")
        cls.reader = new_test_user(
            cls.env,
            login="ai_sales_reader",
            groups="base.group_user,pms.group_pms_user",
            pms_property_ids=[(6, 0, cls.prop.ids)],
            pms_property_id=cls.prop.id,
        )
        Partner = cls.env["res.partner"]
        cls.c1 = Partner.create(
            {"name": "AI One", "is_company": True, "user_id": cls.sp_a.id}
        )
        cls.c2 = Partner.create(
            {"name": "AI Freelance", "is_company": True, "user_id": cls.sp_a.id}
        )
        cls.c3 = Partner.create(
            {"name": "AI Three", "is_company": True, "user_id": cls.sp_a.id}
        )
        cls.c4 = Partner.create(
            {"name": "AI Cooling", "is_company": True, "user_id": cls.sp_a.id}
        )
        cls.c5 = Partner.create(
            {"name": "AI Created by A", "is_company": True, "user_id": cls.sp_b.id}
        )
        cls.c6 = Partner.create(
            {"name": "AI Seasonal no", "is_company": True, "user_id": cls.sp_b.id}
        )
        cls.agency = Partner.create(
            {
                "name": "AI Agency",
                "is_company": True,
                "is_agency": True,
                "user_id": cls.sp_b.id,
                "sale_channel_id": cls.indirect.id,
            }
        )
        cls.guest = Partner.create({"name": "AI Guest"})
        # Tax ids and e-mails as found in real data, without the VAT check.
        for partner, vat, email in (
            (cls.c1, "ESB12345674", "one@example.com"),
            (cls.c2, "ES12345678Z", None),
            (cls.c3, None, "three@example.com"),
            (cls.c4, "ESB00000000", "cool@example.com"),
            (cls.c6, "ESB11111111", "six@example.com"),
            (cls.agency, "ESB22222222", "agency@example.com"),
        ):
            cls.env.cr.execute(
                "UPDATE res_partner SET vat = %s, email = %s WHERE id = %s",
                [vat, email, partner.id],
            )
        cls.env.cr.execute(
            """UPDATE res_partner SET create_uid = %s, create_date = '2026-09-24'
                WHERE id = %s""",
            [cls.sp_a.id, cls.c5.id],
        )
        with freeze_time("2026-09-01"):
            cls.f1 = cls._reservation(cls.c1, [(22, 100), (23, 100)]).folio_id
            cls._reservation(cls.guest, [(22, 150)], agency=cls.agency)
            cls._reservation(cls.c1, [(24, 80)], agency=cls.agency)
            cls.f4 = cls._reservation(cls.c1, [(25, 0)]).folio_id
            cls._reservation(cls.c1, [(30, 100)])
            old_folios = {
                cls.c4: ["2026-03-10", "2026-04-15", "2026-05-20"],
                cls.c6: ["2025-08-20", "2026-01-10", "2026-02-10"],
            }
            for partner, days in old_folios.items():
                for day in days:
                    folio = cls.env["pms.folio"].create(
                        {"pms_property_id": cls.prop.id, "partner_id": partner.id}
                    )
                    cls.env.cr.execute(
                        "UPDATE pms_folio SET date_order = %s WHERE id = %s",
                        [day, folio.id],
                    )
        cls.env.flush_all()
        cls.env.cr.execute(
            """UPDATE pms_reservation_line SET price = 0, price_day_total = 0
                WHERE reservation_id IN (
                    SELECT id FROM pms_reservation WHERE folio_id = %s)""",
            [cls.f4.id],
        )
        cls.inv_c1 = cls._invoice(cls.c1, 1000)
        cls._invoice(cls.c2, 200)
        cls._invoice(cls.c2, 300, "out_refund")
        cls._invoice(cls.c3, 3000)
        cls._invoice(cls.agency, 500)
        # The first invoice belongs to the first folio, which holds a payment
        # that nobody applied to the invoice.
        cls.env.cr.execute(
            """INSERT INTO account_move_folio_ids_rel (account_move_id, folio_ids_id)
               VALUES (%s, %s) ON CONFLICT DO NOTHING""",
            [cls.inv_c1.id, cls.f1.id],
        )
        journal = cls.env["account.journal"].search(
            [("type", "=", "bank"), ("company_id", "=", cls.env.company.id)], limit=1
        )
        payment = cls.env["account.payment"].create(
            {
                "payment_type": "inbound",
                "partner_type": "customer",
                "partner_id": cls.c1.id,
                "amount": 300,
                "journal_id": journal.id,
                "date": "2026-09-23",
                "folio_ids": [(6, 0, cls.f1.ids)],
            }
        )
        payment.action_post()
        cls.env.invalidate_all()
        cls.env["ir.config_parameter"].sudo().set_param(
            PARAM_SALESPEOPLE, f"{cls.sp_a.id},{cls.sp_b.id}"
        )

    @classmethod
    def _reservation(cls, partner, nights, agency=None):
        """Create a reservation with (day of September, price) nights."""
        vals = {
            "pms_property_id": cls.prop.id,
            "room_type_id": cls.room_type.id,
            "partner_id": partner.id,
            "sale_channel_origin_id": (cls.indirect if agency else cls.direct).id,
            "reservation_line_ids": [
                (0, 0, {"date": date(2026, 9, day)}) for day, _price in nights
            ],
        }
        if agency:
            vals["agency_id"] = agency.id
        reservation = cls.env["pms.reservation"].create(vals)
        for line, (_day, price) in zip(
            reservation.reservation_line_ids.sorted("date"), nights, strict=True
        ):
            line.write({"price": price, "discount": 0})
        return reservation

    @classmethod
    def _invoice(cls, partner, amount, move_type="out_invoice"):
        move = cls.env["account.move"].create(
            {
                "move_type": move_type,
                "partner_id": partner.id,
                "invoice_date": "2026-09-23",
                "invoice_date_due": "2026-09-23",
                "invoice_payment_term_id": False,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "AI line",
                            "quantity": 1,
                            "price_unit": amount,
                            "tax_ids": [(6, 0, [])],
                        },
                    )
                ],
            }
        )
        move.action_post()
        return move

    def _run(self, tool, **kwargs):
        return (
            self.env.ref(f"pms_ai_tool.ai_tool_{tool}")
            .with_user(self.reader)
            ._execute_tool(**kwargs)
        )

    def _by_salesperson(self, response):
        return {row["salesperson_id"]: row for row in response["result"]["salespeople"]}

    def test_sales_attribution_and_adr(self):
        response = self._run(
            "pms_salesperson_sales", date_from="2026-09-21", date_to="2026-09-27"
        )
        rows = self._by_salesperson(response)
        sp_a, sp_b = rows[self.sp_a.id], rows[self.sp_b.id]
        self.assertEqual(sp_a["nights"], 3)
        self.assertEqual(sp_a["production"], 200.0)
        self.assertEqual(sp_a["adr"], 100.0)
        self.assertEqual(list(sp_a["by_channel"]), ["company"])
        self.assertEqual(sp_b["nights"], 2)
        self.assertEqual(sp_b["production"], 230.0)
        self.assertEqual(sp_b["adr"], 115.0)
        self.assertEqual(list(sp_b["by_channel"]), ["agency"])
        self.assertIn("1 folios whose stay is worth 0", response["warnings"][0])

    def test_top_customers(self):
        response = self._run(
            "pms_salesperson_top_customers",
            date_from="2026-09-21",
            date_to="2026-09-27",
            top=3,
        )
        rows = self._by_salesperson(response)
        sp_a = rows[self.sp_a.id]
        self.assertEqual(sp_a["invoiced_untaxed"], 3900.0)
        self.assertEqual(
            [(r["partner_id"], r["invoiced_untaxed"]) for r in sp_a["top"]],
            [(self.c3.id, 3000.0), (self.c1.id, 1000.0), (self.c2.id, -100.0)],
        )
        self.assertTrue(sp_a["top"][0]["dependency"])
        self.assertFalse(sp_a["top"][1]["dependency"])
        self.assertTrue(sp_a["top"][2]["self_employed"])
        self.assertEqual(sp_a["top"][2]["refunds"], 1)
        self.assertTrue(rows[self.sp_b.id]["top"][0]["dependency"])
        self.assertIn("negative total", response["warnings"][0])

    def test_portfolio(self):
        response = self._run("pms_salesperson_portfolio", date_to="2026-09-27")
        rows = self._by_salesperson(response)
        sp_a = rows[self.sp_a.id]
        self.assertEqual(sp_a["assigned_companies"], 4)
        self.assertEqual(sp_a["active_companies"], 1)
        self.assertEqual(sp_a["active_pct"], 25.0)
        self.assertEqual(sp_a["new_companies"], 1)
        self.assertEqual(sp_a["without_vat"], 1)
        self.assertEqual(sp_a["without_email"], 1)
        self.assertEqual(sp_a["overdue_invoices"], 3)
        self.assertEqual(sp_a["overdue_amount"], 4200.0)
        self.assertEqual(sp_a["overdue_paid_on_folio_invoices"], 1)
        self.assertEqual(sp_a["overdue_paid_on_folio_amount"], 300.0)
        sp_b = rows[self.sp_b.id]
        self.assertEqual(sp_b["new_companies"], 0)
        self.assertEqual(sp_b["active_companies"], 1)
        self.assertEqual(sp_b["overdue_amount"], 500.0)

    def test_cooling_customers(self):
        response = self._run("pms_salesperson_cooling_customers", date_to="2026-09-27")
        rows = self._by_salesperson(response)
        sp_a = rows[self.sp_a.id]
        self.assertEqual(sp_a["cooling_companies"], 1)
        cooling = sp_a["top"][0]
        self.assertEqual(cooling["partner_id"], self.c4.id)
        self.assertEqual(cooling["folios_previous_12_months"], 3)
        self.assertEqual(cooling["weeks_without_folios"], 18)
        self.assertTrue(cooling["maybe_seasonal"])
        sp_b = rows[self.sp_b.id]
        self.assertEqual([r["partner_id"] for r in sp_b["top"]], [self.c6.id])
        self.assertFalse(sp_b["top"][0]["maybe_seasonal"])
        self.assertIn("1 cooling companies", response["warnings"][0])

    def test_salespeople_argument_and_errors(self):
        response = self._run(
            "pms_salesperson_sales",
            date_from="2026-09-21",
            date_to="2026-09-27",
            salesperson_ids=[self.sp_b.id],
        )
        self.assertEqual(
            [r["salesperson_id"] for r in response["result"]["salespeople"]],
            [self.sp_b.id],
        )
        with self.assertRaisesRegex(UserError, "Unknown salesperson"):
            self._run(
                "pms_salesperson_portfolio",
                date_to="2026-09-27",
                salesperson_ids=[999999],
            )
        self.env["ir.config_parameter"].sudo().set_param(PARAM_SALESPEOPLE, "")
        with self.assertRaisesRegex(UserError, "No salespeople"):
            self._run("pms_salesperson_cooling_customers", date_to="2026-09-27")
        with self.assertRaises(UserError):
            self._run(
                "pms_salesperson_sales", date_from="2026-01-01", date_to="2026-06-30"
            )
        other = self.env["pms.property"].create(
            {
                "name": "AI other property",
                "company_id": self.env.company.id,
                "default_pricelist_id": self.prop.default_pricelist_id.id,
            }
        )
        with self.assertRaises(AccessError):
            self._run(
                "pms_salesperson_sales",
                date_from="2026-09-21",
                date_to="2026-09-27",
                salesperson_ids=[self.sp_a.id],
                property_ids=[other.id],
            )
