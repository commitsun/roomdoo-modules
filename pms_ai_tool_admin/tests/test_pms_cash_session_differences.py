from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, new_test_user, tagged

from .common import AdminToolsMixin

PERIOD = {"date_from": "2026-09-20", "date_to": "2026-09-30"}
LOSS = "Cash difference observed during the counting (Loss) - closing"
PROFIT = "Cash difference observed during the counting (Profit) - closing"


@tagged("post_install", "-at_install")
class TestPmsCashSessionDifferences(AdminToolsMixin, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_admin()
        company = cls.env.company
        Account = cls.env["account.account"]
        cls.loss = Account.create(
            {
                "code": "AI678001",
                "name": "AI cash loss",
                "account_type": "expense",
                "company_id": company.id,
            }
        )
        cls.profit = Account.create(
            {
                "code": "AI778001",
                "name": "AI cash profit",
                "account_type": "income",
                "company_id": company.id,
            }
        )
        cls.cash = cls._journal("ACSH1", cls.prop)
        cls.cash_other = cls._journal("ACSH2", cls.other_prop)
        cls.closer_a = new_test_user(cls.env, login="ai_closer_a", name="AI Closer A")
        cls.closer_b = new_test_user(cls.env, login="ai_closer_b", name="AI Closer B")
        shift_1 = cls._statement(cls.cash, cls.prop, "AI shift 1")
        shift_2 = cls._statement(cls.cash, cls.prop, "AI shift 2")
        shift_3 = cls._statement(cls.cash, cls.prop, "AI shift 3")
        shift_other = cls._statement(cls.cash_other, cls.other_prop, "AI other shift")
        cls.loss_closing = cls._line(shift_1, "2026-09-22", -12.5, LOSS, cls.loss)
        cls.loss_opening = cls._line(
            shift_1, "2026-09-22", -3.0, LOSS.replace("closing", "opening"), cls.loss
        )
        # Takings of the shift poured into the statement: not a difference.
        cls._line(shift_1, "2026-09-22", 80.0, "Room payment", None)
        cls.profit_closing = cls._line(shift_2, "2026-09-25", 4.0, PROFIT, cls.profit)
        # Out of the period; in a property out of the reader's scope; a surplus
        # booked to the loss account is not a difference of the count.
        cls._line(shift_3, "2026-10-15", -7.0, LOSS, cls.loss)
        cls.loss_other = cls._line(shift_other, "2026-09-23", -30.0, LOSS, cls.loss)
        cls._line(shift_3, "2026-09-24", 1.0, "Other income", cls.loss)
        cls.env.cr.execute(
            "UPDATE account_bank_statement_line SET create_uid = %s WHERE id IN %s",
            [cls.closer_a.id, tuple((cls.loss_closing | cls.loss_opening).ids)],
        )
        cls.env.cr.execute(
            "UPDATE account_bank_statement_line SET create_uid = %s WHERE id = %s",
            [cls.closer_b.id, cls.profit_closing.id],
        )
        cls.env.invalidate_all()

    @classmethod
    def _journal(cls, code, prop):
        return cls.env["account.journal"].create(
            {
                "name": f"AI cash {code}",
                "code": code,
                "type": "cash",
                "pms_property_ids": [(6, 0, prop.ids)],
                "loss_account_id": cls.loss.id,
                "profit_account_id": cls.profit.id,
            }
        )

    @classmethod
    def _statement(cls, journal, prop, name):
        return cls.env["account.bank.statement"].create(
            {"name": name, "journal_id": journal.id, "pms_property_id": prop.id}
        )

    @classmethod
    def _line(cls, statement, day, amount, concept, counterpart):
        vals = {
            "statement_id": statement.id,
            "journal_id": statement.journal_id.id,
            "date": day,
            "amount": amount,
            "payment_ref": concept,
        }
        if counterpart:
            vals["counterpart_account_id"] = counterpart.id
        return cls.env["account.bank.statement.line"].create(vals)

    def test_differences_figures(self):
        response = self._run("pms_cash_session_differences", **PERIOD)
        self._assert_read_tool("pms_cash_session_differences", response)
        self.assertEqual(response["period"], PERIOD)
        self.assertEqual(
            response["result"]["totals"],
            {"differences": 3, "losses": -15.5, "profits": 4.0, "net": -11.5},
        )
        (by_property,) = response["result"]["by_property"]
        self.assertEqual(by_property["property_id"], self.prop.id)
        self.assertEqual(by_property["property"], self.prop.name)
        self.assertEqual(by_property["differences"], 3)
        self.assertEqual(by_property["net"], -11.5)
        rows = response["result"]["differences"]
        # Largest in absolute value first.
        self.assertEqual(
            [(r["statement_line_id"], r["difference"]) for r in rows],
            [
                (self.loss_closing.id, -12.5),
                (self.profit_closing.id, 4.0),
                (self.loss_opening.id, -3.0),
            ],
        )
        first = rows[0]
        self.assertEqual(first["statement"], "AI shift 1")
        self.assertEqual(first["property_id"], self.prop.id)
        self.assertEqual(first["journal_code"], "ACSH1")
        self.assertEqual(first["journal"], "AI cash ACSH1")
        self.assertEqual(first["date"], "2026-09-22")
        self.assertEqual(first["closed_by"], "AI Closer A")
        self.assertIn("(Loss) - closing", first["concept"])
        self.assertEqual(rows[1]["closed_by"], "AI Closer B")
        self.assertIn("(Loss) - opening", rows[2]["concept"])

    def test_period_is_respected(self):
        response = self._run(
            "pms_cash_session_differences", date_from="2026-10-01", date_to="2026-10-31"
        )
        self.assertEqual(response["result"]["totals"]["differences"], 1)
        (row,) = response["result"]["differences"]
        self.assertEqual(row["difference"], -7.0)
        empty = self._run(
            "pms_cash_session_differences", date_from="2026-08-01", date_to="2026-08-31"
        )
        self.assertEqual(empty["result"]["totals"]["differences"], 0)
        self.assertEqual(empty["result"]["differences"], [])

    def test_scope_by_property(self):
        both = self._run(
            "pms_cash_session_differences", user=self.reader_both, **PERIOD
        )
        self.assertEqual(both["result"]["totals"]["differences"], 4)
        self.assertEqual(both["result"]["totals"]["losses"], -45.5)
        self.assertEqual(len(both["result"]["by_property"]), 2)
        only_other = self._run(
            "pms_cash_session_differences",
            user=self.reader_both,
            property_ids=[self.other_prop.id],
            **PERIOD,
        )
        self.assertEqual(
            [r["statement_line_id"] for r in only_other["result"]["differences"]],
            [self.loss_other.id],
        )
        with self.assertRaises(AccessError):
            self._run(
                "pms_cash_session_differences",
                property_ids=[self.other_prop.id],
                **PERIOD,
            )

    def test_needs_accounting_access(self):
        with self.assertRaises(AccessError):
            self._run("pms_cash_session_differences", user=self.pms_only, **PERIOD)

    def test_listing_is_limited(self):
        self.env["ir.config_parameter"].sudo().set_param("pms_ai_tool.max_rows", "2")
        response = self._run("pms_cash_session_differences", **PERIOD)
        self.assertEqual(len(response["result"]["differences"]), 2)
        self.assertEqual(response["result"]["totals"]["differences"], 3)
        self.assertTrue(any("first 2 of 3" in text for text in response["warnings"]))

    def test_validation(self):
        bad_calls = [
            {"date_from": "2026-01-01", "date_to": "2026-06-30"},
            {"date_from": "2026-09-30", "date_to": "2026-09-20"},
            {"date_from": "30/09/2026", "date_to": "2026-10-01"},
            {"date_to": "2026-09-30"},
            dict(PERIOD, domain="[]"),
        ]
        for kwargs in bad_calls:
            with self.subTest(kwargs=kwargs), self.assertRaises(UserError):
                self._run("pms_cash_session_differences", **kwargs)
