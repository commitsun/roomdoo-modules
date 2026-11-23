from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, tagged

from .common import AdminToolsMixin, SupplierBillsMixin

PERIOD = {"date_from": "2026-09-01", "date_to": "2026-09-30"}


@tagged("post_install", "-at_install")
class TestAccSupplierInvoices(AdminToolsMixin, SupplierBillsMixin, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_admin()
        cls._bill_fixture()

    def _tool(self, **kwargs):
        return self._run("acc_supplier_invoices", **dict(PERIOD, **kwargs))

    def test_lines_by_supplier(self):
        response = self._tool()
        self._assert_read_tool("acc_supplier_invoices", response)
        result = response["result"]
        self.assertEqual(
            {row["bill_id"] for row in result["bills"]},
            {self.b1.id, self.b2.id, self.b5.id},
        )
        groups = {row["label"]: row for row in result["line_groups"]}
        self.assertEqual(groups["AI Supplier A"]["untaxed"], 1300.0)
        self.assertEqual(groups["AI Supplier B"]["untaxed"], 500.0)
        self.assertEqual(result["totals"]["untaxed"], 1800.0)
        self.assertEqual(result["totals"]["bills"], 3)
        self.assertEqual(len(result["lines"]), 3)

    def test_filters(self):
        account = self.b1.invoice_line_ids.account_id
        by_account = self._tool(
            account_prefixes=[account.code[:3]], group_lines_by="account"
        )["result"]
        self.assertEqual(by_account["totals"]["untaxed"], 1800.0)
        self.assertEqual(len(by_account["line_groups"]), 1)
        none = self._tool(account_prefixes=["999"])["result"]
        self.assertEqual(none["totals"]["bills"], 0)
        self.assertEqual(self._tool(supplier="supplier b")["result"]["totals"]["bills"], 1)
        self.assertEqual(self._tool(min_amount=600)["result"]["totals"]["bills"], 1)
        self.assertEqual(self._tool(max_amount=400)["result"]["totals"]["bills"], 1)
        self.assertEqual(self._tool(text="AI service")["result"]["totals"]["bills"], 3)
        self.assertEqual(self._tool(text="nothing like this")["result"]["totals"]["bills"], 0)
        by_month = self._tool(group_lines_by="month")["result"]["line_groups"]
        self.assertEqual([row["label"] for row in by_month], ["2026-09"])

    def test_payment_analysis_per_bill(self):
        bills = {row["bill_id"]: row for row in self._tool()["result"]["bills"]}
        first = bills[self.b1.id]
        self.assertEqual((first["paid"], first["pending"]), (700.0, 300.0))
        self.assertTrue(first["split_payment"])
        self.assertEqual(len(first["payments"]), 2)
        self.assertEqual(first["payments"][0]["journal_type"], "bank")
        self.assertEqual(first["real_days_to_last_payment"], 56)
        self.assertEqual(first["delay_days_vs_due"], 26)
        self.assertFalse(first["no_payment_record"])
        paid = bills[self.b5.id]
        self.assertEqual((paid["paid"], paid["pending"]), (300.0, 0.0))
        self.assertEqual(paid["delay_days_vs_due"], -22)
        self.assertEqual(bills[self.b2.id]["paid"], 0.0)
        self.assertNotIn("real_days_to_last_payment", bills[self.b2.id])

    def test_payment_groups(self):
        groups = {
            row["label"]: row for row in self._tool()["result"]["payment_groups"]
        }
        supplier = groups["AI Supplier A"]
        self.assertEqual(supplier["paid"], 1000.0)
        self.assertEqual(supplier["pending"], 300.0)
        # (400 * 15 + 300 * 56 + 300 * 8) / 1000 days, 300 of 1000 paid late
        self.assertEqual(supplier["real_days"], 25.2)
        self.assertEqual(supplier["late_pct"], 30.0)
        self.assertIsNone(groups["AI Supplier B"]["real_days"])
        by_method = self._tool(group_payments_by="method")["result"]["payment_groups"]
        labels = {row["label"]: row for row in by_method}
        bank = [row for key, row in labels.items() if key.startswith("bank: ")]
        self.assertEqual(sum(row["paid"] for row in bank), 1000.0)
        self.assertIn("Not paid", labels)
        for group in ("property", "month", "account"):
            rows = self._tool(group_payments_by=group)["result"]["payment_groups"]
            self.assertEqual(sum(row["paid"] for row in rows), 1000.0, group)

    def test_without_payments_scope_and_errors(self):
        result = self._tool(include_payments=False)["result"]
        self.assertNotIn("payment_groups", result)
        self.assertNotIn("paid", result["bills"][0])
        both = self._tool(user=self.reader_both)["result"]
        self.assertEqual(both["totals"]["bills"], 4)
        with self.assertRaises(AccessError):
            self._tool(property_ids=[self.other_prop.id])
        with self.assertRaises(AccessError):
            self._tool(user=self.pms_only)
        with self.assertRaises(UserError):
            self._run(
                "acc_supplier_invoices",
                date_from="2026-01-01",
                date_to="2026-12-31",
            )
        with self.assertRaises(UserError):
            self._tool(account_prefixes=["60%"])
