from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, tagged

from .common import AdminToolsMixin, SupplierBillsMixin

TOOL = "acc_admin_daily_brief"


@tagged("post_install", "-at_install")
class TestAccAdminDailyBrief(AdminToolsMixin, SupplierBillsMixin, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_admin()
        cls._setup_bills()
        # Leave out any document of the database: only the fixture counts.
        cls.env.cr.execute(
            "UPDATE account_move SET state = 'cancel' "
            "WHERE state IN ('posted', 'draft') AND move_type <> 'entry'"
        )
        cls.env.cr.execute(
            "UPDATE account_move SET state = 'cancel' WHERE state = 'posted' "
            "AND id IN (SELECT move_id FROM account_payment)"
        )
        if "account.payment.order" in cls.env:
            cls.env.cr.execute(
                "UPDATE account_payment_order SET state = 'cancel' "
                "WHERE state = 'draft'"
            )
        cls.env.invalidate_all()
        cls.today = today = fields.Date.context_today(
            cls.env["pms.ai.tools"].with_user(cls.reader)
        )

        def day(offset):
            return today + timedelta(days=offset)

        tax = cls.env["account.tax"].search(
            [
                ("type_tax_use", "=", "purchase"),
                ("company_id", "=", cls.env.company.id),
            ],
            limit=1,
        )
        a, b = cls.supplier_a, cls.supplier_b
        cls.draft_ok = cls._bill(a, 110, day(-30), day(30), cls.prop, post=False)
        cls.draft_ok.invoice_line_ids.tax_ids = tax
        cls.draft_no_date = cls._bill(b, 120, day(-5), day(25), cls.prop, post=False)
        cls.draft_no_date.invoice_date = False
        cls.overdue = cls._bill(a, 130, day(-40), day(-10), cls.prop)
        cls.due_soon = cls._bill(b, 140, day(-3), day(3), cls.prop)
        cls.later = cls._bill(a, 150, day(-2), day(30), cls.prop)
        cls.flagged = cls._bill(b, 160, day(-50), day(20), cls.prop)
        cls.flagged.to_check = True
        cls.dup_posted = cls._bill(a, 170, day(-15), day(15), cls.prop)
        cls.dup_draft = cls._bill(a, 170, day(-15), day(15), cls.prop, post=False)
        cls.other_draft = cls._bill(
            a, 180, day(-8), day(22), cls.other_prop, post=False
        )
        # A bill of another company, moved there by SQL to keep the test fast.
        cls.other_company = cls.env["res.company"].create({"name": "AI other company"})
        cls.foreign = cls._bill(b, 190, day(-60), day(-30), cls.prop)
        cls.env.cr.execute(
            "UPDATE account_move SET company_id = %(company)s WHERE id = %(move)s;"
            "UPDATE account_move_line SET company_id = %(company)s "
            "WHERE move_id = %(move)s",
            {"company": cls.other_company.id, "move": cls.foreign.id},
        )
        cls.customer = cls.env["res.partner"].create(
            {"name": "AI Customer Co", "is_company": True}
        )
        cls.guest = cls.env["res.partner"].create({"name": "AI Guest Person"})
        cls.customer_invoice = cls._customer_invoice(cls.customer, 200, day(-5))
        cls._customer_invoice(cls.guest, 90, day(-2))
        cls._customer_invoice(cls.customer, 70, day(5))
        journal = cls.env["account.journal"].search(
            [("type", "=", "bank"), ("company_id", "=", cls.env.company.id)], limit=1
        )
        cls.collection = cls.env["account.payment"].create(
            {
                "payment_type": "inbound",
                "partner_type": "customer",
                "partner_id": cls.customer.id,
                "amount": 50,
                "journal_id": journal.id,
                "date": day(-4),
            }
        )
        cls.collection.action_post()
        cls.env.invalidate_all()

    @classmethod
    def _customer_invoice(cls, partner, price, due):
        move = cls.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": partner.id,
                "invoice_date": due - timedelta(days=30),
                "invoice_date_due": due,
                "invoice_payment_term_id": False,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "AI stay",
                            "quantity": 1,
                            "price_unit": price,
                            "tax_ids": [(6, 0, [])],
                        },
                    )
                ],
            }
        )
        move.action_post()
        cls.env.cr.execute(
            "UPDATE account_move SET pms_property_id = %s WHERE id = %s",
            [cls.prop.id, move.id],
        )
        move.invalidate_recordset()
        return move

    def test_sections(self):
        response = self._run(TOOL)
        self._assert_read_tool(TOOL, response)
        result = response["result"]
        self.assertEqual(result["date"], str(self.today))

        drafts = result["draft_vendor_bills"]
        self.assertEqual(drafts["count"], 3)
        self.assertEqual(
            {item["id"] for item in drafts["items"]},
            {self.draft_ok.id, self.draft_no_date.id, self.dup_draft.id},
        )
        self.assertEqual(drafts["oldest_age_days"], 0)
        self.assertEqual(
            drafts["next_tools"], ["acc_vendor_bill_edit", "acc_vendor_bills_post"]
        )

        review = result["vendor_bills_to_review"]
        self.assertEqual(
            (review["count"], review["to_check"], review["incomplete_drafts"]),
            (3, 1, 2),
        )
        reasons = {item["id"]: item["reasons"] for item in review["items"]}
        self.assertEqual(reasons[self.flagged.id], ["to_check"])
        self.assertIn("no_invoice_date", reasons[self.draft_no_date.id])
        self.assertIn("no_tax", reasons[self.dup_draft.id])
        self.assertNotIn(self.draft_ok.id, reasons)
        self.assertEqual(review["next_tools"], ["acc_vendor_bill_edit"])

        duplicates = result["duplicate_vendor_bills"]
        self.assertEqual((duplicates["count"], duplicates["bills"]), (1, 2))
        (group,) = duplicates["items"]
        self.assertEqual(group["rules"], ["reference", "total_and_date"])
        self.assertEqual(
            {bill["id"] for bill in group["bills"]},
            {self.dup_posted.id, self.dup_draft.id},
        )
        self.assertEqual(group["supplier"], "AI Supplier A")

        to_pay = result["vendor_bills_to_pay"]
        self.assertEqual(
            (to_pay["overdue"]["count"], to_pay["overdue"]["pending"]), (1, 130.0)
        )
        self.assertEqual(to_pay["overdue"]["items"][0]["id"], self.overdue.id)
        self.assertEqual(
            (to_pay["due_soon"]["count"], to_pay["due_soon"]["pending"]), (1, 140.0)
        )
        self.assertEqual(to_pay["due_soon"]["items"][0]["id"], self.due_soon.id)
        self.assertEqual(
            to_pay["next_tools"], ["acc_payment_register", "acc_payment_order_prepare"]
        )

        customers = result["customer_invoices_overdue"]
        self.assertEqual((customers["count"], customers["pending"]), (2, 290.0))
        self.assertEqual(
            [item["customer"] for item in customers["items"]], ["AI Customer Co", None]
        )
        self.assertEqual(customers["items"][0]["days_overdue"], 5)
        self.assertEqual(customers["next_tools"], ["pms_customer_overdue"])

        payments = result["unapplied_payments"]
        self.assertEqual((payments["count"], payments["open_amount"]), (1, 50.0))
        self.assertEqual(payments["by_kind"]["customer"]["count"], 1)
        (item,) = payments["items"]
        self.assertEqual(
            (item["payment_id"], item["partner"], item["age_days"]),
            (self.collection.id, "AI Customer Co", 4),
        )
        self.assertEqual(payments["next_tools"], ["acc_reconcile_suggest"])

        self.assertEqual(
            "draft_payment_orders" in result, "account.payment.order" in self.env
        )
        self.assertEqual(
            [row["section"] for row in result["worklist"]][:6],
            [
                "draft_vendor_bills",
                "vendor_bills_to_review",
                "duplicate_vendor_bills",
                "vendor_bills_to_pay",
                "customer_invoices_overdue",
                "unapplied_payments",
            ],
        )
        self.assertIn("3 draft vendor bills", response["summary"])

    def test_applied_payment_and_paid_bill_leave_the_brief(self):
        self._pay_bill(self.overdue, 130, self.today)
        result = self._run(TOOL)["result"]
        self.assertEqual(result["vendor_bills_to_pay"]["overdue"]["count"], 0)
        # The supplier payment is fully applied: not in unapplied_payments.
        self.assertEqual(result["unapplied_payments"]["count"], 1)

    def test_scope_of_properties_and_companies(self):
        both = self._run(TOOL, user=self.reader_both)["result"]
        self.assertEqual(both["draft_vendor_bills"]["count"], 4)
        overdue = both["vendor_bills_to_pay"]["overdue"]
        # The bill of the other company never shows.
        self.assertEqual(overdue["count"], 1)
        self.assertNotIn(self.foreign.id, [item["id"] for item in overdue["items"]])

        other = self._run(
            TOOL, user=self.reader_both, property_ids=[self.other_prop.id]
        )["result"]
        self.assertEqual(other["draft_vendor_bills"]["count"], 1)
        self.assertEqual(
            other["draft_vendor_bills"]["items"][0]["id"], self.other_draft.id
        )
        # Documents without property only show when no property is requested.
        self.assertEqual(other["unapplied_payments"]["count"], 0)
        self.assertEqual(other["vendor_bills_to_pay"]["overdue"]["count"], 0)

        own = self._run(TOOL, company_id=self.env.company.id)["result"]
        self.assertEqual(own["draft_vendor_bills"]["count"], 3)
        with self.assertRaises(AccessError):
            self._run(TOOL, company_id=self.other_company.id)
        with self.assertRaises(AccessError):
            self._run(TOOL, property_ids=[self.other_prop.id])
