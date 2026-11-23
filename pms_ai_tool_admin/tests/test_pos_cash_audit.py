from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, tagged

from .common import AdminToolsMixin

PERIOD = {"date_from": "2026-09-01", "date_to": "2026-09-30"}


@tagged("post_install", "-at_install")
class TestPosCashAudit(AdminToolsMixin, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_admin()
        if "pos.session" not in cls.env:
            cls.skipTest(cls, "The point of sale module is not installed.")
        cls.cash_journal, cls.other_cash_journal = cls.env["account.journal"].create(
            [
                {
                    "name": f"AI POS cash {code}",
                    "code": code,
                    "type": "cash",
                    "company_id": cls.env.company.id,
                    "pms_property_ids": [(6, 0, prop.ids)],
                }
                for code, prop in (("AIPC", cls.prop), ("AIPD", cls.other_prop))
            ]
        )
        cls.cash_method = cls._method("AI POS cash", cls.cash_journal)
        cls.other_cash_method = cls._method("AI POS other cash", cls.other_cash_journal)
        bank_journal = cls.env["account.journal"].search(
            [("type", "=", "bank"), ("company_id", "=", cls.env.company.id)], limit=1
        )
        cls.card_method = cls._method("AI POS card", bank_journal)
        cls.session = cls._session(
            "AI POS bar",
            cls.cash_method,
            [(100, cls.cash_method), (40, cls.card_method)],
        )
        cls.other_session = cls._session(
            "AI POS other bar", cls.other_cash_method, [(10, cls.other_cash_method)]
        )
        order = cls._order(cls.session, -15)
        cls._pay(order, -15, cls.cash_method)
        cls._order(cls.session, 25, state="cancel")
        # The first session is closed with 5 missing in the cash box.
        (cls.session | cls.other_session).write({"start_at": "2026-09-10 09:00:00"})
        cls.env.cr.execute(
            "UPDATE pos_session SET state = 'closed', "
            "cash_register_balance_end_real = %s WHERE id = %s",
            [80.0, cls.session.id],
        )
        cls.env.invalidate_all()

    @classmethod
    def _method(cls, name, journal):
        return cls.env["pos.payment.method"].create(
            {"name": name, "journal_id": journal.id, "company_id": cls.env.company.id}
        )

    @classmethod
    def _session(cls, name, cash_method, payments):
        config = cls.env["pos.config"].create(
            {
                "name": name,
                "payment_method_ids": [(6, 0, [m.id for _a, m in payments])],
            }
        )
        session = cls.env["pos.session"].create(
            {"config_id": config.id, "user_id": cls.env.user.id}
        )
        session.action_pos_session_open()
        for value, method in payments:
            cls._pay(cls._order(session, value), value, method)
        return session

    @classmethod
    def _order(cls, session, total, state="paid"):
        order = cls.env["pos.order"].create(
            {
                "name": f"AI POS order {total} {state}",
                "session_id": session.id,
                "company_id": cls.env.company.id,
                "pricelist_id": session.config_id.pricelist_id.id
                or cls.env["product.pricelist"].search([], limit=1).id,
                "amount_tax": 0,
                "amount_total": total,
                "amount_paid": total,
                "amount_return": 0,
            }
        )
        order.state = state
        return order

    @classmethod
    def _pay(cls, order, value, method):
        cls.env["pos.payment"].create(
            {"pos_order_id": order.id, "amount": value, "payment_method_id": method.id}
        )

    def _tool(self, **kwargs):
        return self._run("pos_cash_audit", **dict(PERIOD, **kwargs))

    def test_session_audit(self):
        response = self._tool()
        self._assert_read_tool("pos_cash_audit", response)
        result = response["result"]
        rows = {row["session_id"]: row for row in result["sessions"]}
        self.assertEqual(set(rows), {self.session.id})
        row = rows[self.session.id]
        self.assertEqual(row["state"], "closed")
        self.assertEqual(row["property"], self.prop.name)
        self.assertEqual(row["orders"], 3)
        self.assertEqual(row["sales"], 125.0)
        self.assertEqual((row["refunds"], row["refund_orders"]), (-15.0, 1))
        self.assertEqual(row["cancelled_orders"], 1)
        self.assertEqual(row["closing_balance_counted"], 80.0)
        self.assertEqual(row["closing_balance_expected"], 85.0)
        self.assertEqual(row["closing_difference"], -5.0)
        kinds = {p["method"]: (p["kind"], p["amount"]) for p in row["payments"]}
        self.assertEqual(kinds["AI POS cash"], ("cash", 85.0))
        self.assertEqual(kinds["AI POS card"], ("card_or_bank", 40.0))
        # Closed without the accounting entry of the session.
        self.assertTrue(row["entry_not_posted"])
        self.assertEqual(result["totals"]["closing_differences"], -5.0)
        self.assertEqual(result["totals"]["closed_without_posted_entry"], 1)
        self.assertEqual(result["by_user"][0]["closing_differences"], -5.0)

    def test_scope_and_errors(self):
        both = self._tool(user=self.reader_both)["result"]["sessions"]
        self.assertEqual(
            {row["session_id"] for row in both},
            {self.session.id, self.other_session.id},
        )
        open_row = next(r for r in both if r["session_id"] == self.other_session.id)
        self.assertIn(open_row["state"], ("opening_control", "opened"))
        self.assertIsNone(open_row["closing_difference"])
        with self.assertRaises(AccessError):
            self._tool(property_ids=[self.other_prop.id])
        with self.assertRaises(AccessError):
            self._tool(user=self.pms_only)
        with self.assertRaises(UserError):
            self._run("pos_cash_audit", date_from="2026-01-01", date_to="2026-12-31")
