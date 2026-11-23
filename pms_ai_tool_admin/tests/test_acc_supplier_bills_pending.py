from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, tagged

from .common import AdminToolsMixin, SupplierBillsMixin

CUTOFF = "2026-10-15"


@tagged("post_install", "-at_install")
class TestAccSupplierBillsPending(AdminToolsMixin, SupplierBillsMixin, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_admin()
        cls._bill_fixture()

    def _tool(self, **kwargs):
        return self._run("acc_supplier_bills_pending", cutoff=CUTOFF, **kwargs)

    def test_pending_uses_payment_dates(self):
        response = self._tool()
        self._assert_read_tool("acc_supplier_bills_pending", response)
        self.assertEqual(response["period"], {"cutoff": CUTOFF})
        result = response["result"]
        bills = {row["bill_id"]: row for row in result["bills"]}
        # The 300 paid on 2026-11-05 is after the cutoff: still pending then.
        self.assertEqual(set(bills), {self.b1.id, self.b2.id})
        self.assertEqual(bills[self.b1.id]["paid_at_cutoff"], 400.0)
        self.assertEqual(bills[self.b1.id]["pending_at_cutoff"], 600.0)
        self.assertEqual(bills[self.b1.id]["payments_at_cutoff"], 1)
        self.assertTrue(bills[self.b1.id]["overdue_at_cutoff"])
        self.assertEqual(bills[self.b2.id]["pending_at_cutoff"], 500.0)
        self.assertTrue(bills[self.b2.id]["overdue_at_cutoff"])
        self.assertEqual(result["totals"]["pending_at_cutoff"], 1100.0)
        self.assertEqual(result["totals"]["paid_at_cutoff"], 400.0)
        suppliers = {r["supplier"]: r for r in result["by_supplier"]}
        self.assertEqual(suppliers["AI Supplier A"]["pending_at_cutoff"], 600.0)
        self.assertEqual(suppliers["AI Supplier B"]["pending_at_cutoff"], 500.0)
        self.assertEqual(len(result["by_company"]), 1)
        self.assertEqual(result["by_property"][0]["property"], self.prop.name)

    def test_later_cutoff_sees_the_final_payment(self):
        response = self._run("acc_supplier_bills_pending", cutoff="2026-11-30")
        bills = {row["bill_id"]: row for row in response["result"]["bills"]}
        self.assertEqual(bills[self.b1.id]["pending_at_cutoff"], 300.0)
        self.assertIn(self.b4.id, bills)

    def test_drafts_and_scope(self):
        drafts = self._tool()["result"]["drafts"]
        self.assertEqual((drafts["bills"], drafts["total"]), (1, 80.0))
        self.assertNotIn("drafts", self._tool(include_drafts=False)["result"])
        both = self._tool(user=self.reader_both)["result"]
        self.assertEqual(both["totals"]["pending_at_cutoff"], 1300.0)
        with self.assertRaises(AccessError):
            self._tool(property_ids=[self.other_prop.id])
        with self.assertRaises(AccessError):
            self._tool(user=self.pms_only)
