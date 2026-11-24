from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.pms_ai_tool.models.pms_ai_tools import PARAM_MAX_ROWS

from .common import AdminToolsMixin, SupplierBillsMixin

CUTOFF = "2026-10-15"
PENDING_MODULE = "odoo.addons.pms_ai_tool_admin.models.pms_supplier_bills_pending"


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

    def _payable_code(self, bill):
        return bill.line_ids.filtered(
            lambda line: line.account_id.account_type == "liability_payable"
        ).account_id.code

    def test_extra_columns_and_last_payment(self):
        self.env.cr.execute(
            "UPDATE res_partner SET vat = 'ESA00000001' WHERE id = %s",
            [self.supplier_a.id],
        )
        self.env.invalidate_all()
        bills = {row["bill_id"]: row for row in self._tool()["result"]["bills"]}
        b1, b2 = bills[self.b1.id], bills[self.b2.id]
        self.assertEqual(b1["supplier_id"], self.supplier_a.id)
        self.assertEqual(b1["supplier_vat"], "ESA00000001")
        self.assertEqual(b1["company_id"], self.env.company.id)
        self.assertEqual(b1["payable_account"], self._payable_code(self.b1))
        self.assertTrue(b1["payable_account"])
        self.assertEqual(b1["journal"], self.b1.journal_id.code)
        # Paid 400 on 2026-09-25 and 300 on 2026-11-05: only the first one
        # is on or before the cutoff.
        self.assertEqual(b1["last_payment_date"], "2026-09-25")
        self.assertEqual(b2["supplier_id"], self.supplier_b.id)
        self.assertIsNone(b2["supplier_vat"])
        self.assertIsNone(b2["last_payment_date"])
        self.assertEqual(b2["payments_at_cutoff"], 0)
        later = self._run("acc_supplier_bills_pending", cutoff="2026-11-30")
        bills = {row["bill_id"]: row for row in later["result"]["bills"]}
        self.assertEqual(bills[self.b1.id]["last_payment_date"], "2026-11-05")
        self.assertEqual(bills[self.b1.id]["payments_at_cutoff"], 2)

    def test_filters(self):
        self.env.cr.execute(
            "UPDATE res_partner SET vat = 'ESA00000001' WHERE id = %s",
            [self.supplier_a.id],
        )
        self.env.invalidate_all()
        by_id = self._tool(supplier=str(self.supplier_b.id))["result"]
        self.assertEqual([row["bill_id"] for row in by_id["bills"]], self.b2.ids)
        self.assertEqual(by_id["totals"]["pending_at_cutoff"], 500.0)
        self.assertEqual(by_id["drafts"]["bills"], 1)
        by_vat = self._tool(supplier="A00000001")["result"]
        self.assertEqual([row["bill_id"] for row in by_vat["bills"]], self.b1.ids)
        self.assertEqual(by_vat["drafts"]["bills"], 0)
        with self.assertRaisesRegex(UserError, "No partner found"):
            self._tool(supplier="ESZ99999999")
        big = self._tool(min_pending=550)["result"]
        self.assertEqual([row["bill_id"] for row in big["bills"]], self.b1.ids)
        self.assertEqual(big["totals"]["pending_at_cutoff"], 600.0)
        self.assertEqual(big["totals"]["suppliers"], 1)
        same = self._tool(min_pending=600)["result"]
        self.assertEqual([row["bill_id"] for row in same["bills"]], self.b1.ids)
        company = self._tool(company_ids=[self.env.company.id])["result"]
        self.assertEqual(company["totals"]["pending_at_cutoff"], 1100.0)
        with self.assertRaisesRegex(AccessError, "companies 999999"):
            self._tool(company_ids=[999999])
        with self.assertRaisesRegex(UserError, "at least 0"):
            self._tool(min_pending=-1)

    def test_company_filter_narrows(self):
        company = self.env["res.company"].create({"name": "AI admin company 2"})
        self.reader.write({"company_ids": [(4, company.id)]})
        result = self._tool(company_ids=[company.id])["result"]
        self.assertEqual(result["bills"], [])
        self.assertEqual(result["totals"]["bills"], 0)
        self.assertEqual(result["drafts"]["bills"], 0)

    def test_pagination(self):
        first = self._tool(limit=1)
        self.assertEqual(
            [row["bill_id"] for row in first["result"]["bills"]], self.b1.ids
        )
        self.assertEqual(
            first["result"]["page"],
            {
                "offset": 0,
                "limit": 1,
                "returned": 1,
                "total": 2,
                "has_more": True,
                "next_offset": 1,
            },
        )
        self.assertTrue(any("offset=1" in text for text in first["warnings"]))
        # Totals and summaries always cover every bill, whatever the page.
        self.assertEqual(first["result"]["totals"]["pending_at_cutoff"], 1100.0)
        second = self._tool(offset=1, limit=1)
        self.assertEqual(
            [row["bill_id"] for row in second["result"]["bills"]], self.b2.ids
        )
        self.assertFalse(second["result"]["page"]["has_more"])
        self.assertIsNone(second["result"]["page"]["next_offset"])
        past = self._tool(offset=5)
        self.assertEqual(past["result"]["bills"], [])
        self.assertTrue(any("past the last" in text for text in past["warnings"]))
        # The row limit of the tools caps the page size.
        self.env["ir.config_parameter"].sudo().set_param(PARAM_MAX_ROWS, "1")
        capped = self._tool(limit=10)["result"]
        self.assertEqual(capped["page"]["limit"], 1)
        self.assertEqual(len(capped["bills"]), 1)

    def test_max_suppliers(self):
        response = self._tool(max_suppliers=1)
        suppliers = response["result"]["by_supplier"]
        self.assertEqual([row["supplier_id"] for row in suppliers], self.supplier_a.ids)
        self.assertEqual(response["result"]["totals"]["suppliers"], 2)
        self.assertTrue(
            any("1 of 2 suppliers" in text for text in response["warnings"])
        )
        self.assertEqual(len(self._tool()["result"]["by_supplier"]), 2)
        self.env["ir.config_parameter"].sudo().set_param(PARAM_MAX_ROWS, "1")
        capped = self._tool(max_suppliers=30)["result"]
        self.assertEqual(len(capped["by_supplier"]), 1)

    def test_incomplete_list_warning(self):
        response = self._tool()
        self.assertTrue(response["result"]["complete"])
        self.assertFalse(any("INCOMPLETE" in text for text in response["warnings"]))
        with patch(f"{PENDING_MODULE}.MAX_BILLS", 1):
            response = self._tool()
        self.assertFalse(response["result"]["complete"])
        warning = next(text for text in response["warnings"] if "INCOMPLETE" in text)
        self.assertIn("company_ids", warning)
        # Only the oldest candidate was analysed.
        self.assertEqual(
            [row["bill_id"] for row in response["result"]["bills"]], self.b1.ids
        )
