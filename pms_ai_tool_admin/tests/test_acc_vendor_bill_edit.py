from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.pms_ai_tool.tools import PARAM_WRITE_ENABLED

from .common import AdminToolsMixin, SupplierBillsMixin

TOOL = "acc_vendor_bill_edit"


class VendorBillWriteMixin(AdminToolsMixin, SupplierBillsMixin):
    """Draft vendor bills with accounts and taxes created for the tests."""

    @classmethod
    def _setup_vendor_bills(cls, tool):
        cls._setup_admin()
        cls._setup_bills()
        cls.env["ir.config_parameter"].sudo().set_param(PARAM_WRITE_ENABLED, "True")
        cls.env.ref(f"pms_ai_tool_admin.ai_tool_{tool}").sudo().write(
            {
                "allowed_user_ids": [(6, 0, (cls.reader | cls.reader_both).ids)],
                "write_enabled": True,
            }
        )
        company = cls.env.company
        cls.expense, cls.expense2 = cls.env["account.account"].create(
            [
                {
                    "name": f"AI expense {code}",
                    "code": code,
                    "account_type": "expense",
                    "company_id": company.id,
                }
                for code in ("629901", "629902")
            ]
        )
        cls.tax21, cls.tax10 = cls.env["account.tax"].create(
            [
                {
                    "name": f"AI purchase VAT {rate}",
                    "amount": rate,
                    "amount_type": "percent",
                    "type_tax_use": "purchase",
                    "company_id": company.id,
                }
                for rate in (21, 10)
            ]
        )

    @classmethod
    def _draft(cls, partner, prices, prop, ref, taxes=None, **values):
        taxes = cls.tax21 if taxes is None else taxes
        move = cls.env["account.move"].create(
            dict(
                {
                    "move_type": "in_invoice",
                    "partner_id": partner.id,
                    "invoice_date": "2026-10-05",
                    "date": "2026-10-05",
                    "invoice_date_due": "2026-11-05",
                    "invoice_payment_term_id": False,
                    "ref": ref,
                    "invoice_line_ids": [
                        (
                            0,
                            0,
                            {
                                "name": f"AI line {price}",
                                "quantity": 1,
                                "price_unit": price,
                                "account_id": cls.expense.id,
                                "tax_ids": [(6, 0, taxes.ids)],
                            },
                        )
                        for price in prices
                    ],
                },
                **values,
            )
        )
        cls.env.cr.execute(
            "UPDATE account_move SET pms_property_id = %s WHERE id = %s",
            [prop.id, move.id],
        )
        move.invalidate_recordset()
        return move

    def _preview(self, tool, user=None, **kwargs):
        return self._run(tool, user=user, **kwargs)

    def _apply(self, tool, user=None, **kwargs):
        token = self._preview(tool, user=user, **kwargs)["confirmation"][
            "confirmation_token"
        ]
        return self._run(
            tool, user=user, confirm=True, confirmation_token=token, **kwargs
        )

    def _assert_write_tool(self, tool, response):
        self.assertEqual(response["tool"], tool)
        self.assertTrue(response["summary"])
        self.assertTrue(response["definitions"])
        self.assertTrue(response["confirmation"]["required"])
        record = self.env.ref(f"pms_ai_tool_admin.ai_tool_{tool}")
        self.assertEqual(record.guard_mode, "write")
        annotations = record._get_tool_definition()["annotations"]
        self.assertTrue(annotations["destructiveHint"])
        self.assertFalse(annotations["readOnlyHint"])


@tagged("post_install", "-at_install")
class TestAccVendorBillEdit(VendorBillWriteMixin, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_vendor_bills(TOOL)
        cls.bill = cls._draft(cls.supplier_a, [100, 200], cls.prop, "AI-EDIT-1")
        cls.line1, cls.line2 = cls.bill.invoice_line_ids.sorted("id")
        cls.bank_b = cls.env["res.partner.bank"].create(
            {
                "partner_id": cls.supplier_b.id,
                "acc_number": "AI 0000 0001",
                "allow_out_payment": True,
            }
        )
        cls.env.cr.execute(
            "UPDATE res_partner SET vat = 'ESX0000000AI' WHERE id = %s",
            [cls.supplier_b.id],
        )
        cls.env.invalidate_all()

    def _state(self, move):
        move.invalidate_recordset()
        move.line_ids.invalidate_recordset()
        return {
            "ref": move.ref,
            "to_check": move.to_check,
            "partner": move.partner_id,
            "accounts": move.invoice_line_ids.mapped("account_id"),
            "taxes": move.invoice_line_ids.mapped("tax_ids"),
            "total": move.amount_total,
        }

    def test_preview_changes_nothing_and_apply_does_what_it_listed(self):
        kwargs = {
            "bill_id": self.bill.id,
            "ref": "AI-EDIT-NEW",
            "to_check": True,
            "lines": (self.line1 | self.line2).ids,
            "account": "629902",
            "taxes": ["AI purchase VAT 10"],
        }
        before = self._state(self.bill)
        preview = self._preview(TOOL, **kwargs)
        self._assert_write_tool(TOOL, preview)
        result = preview["result"]
        self.assertEqual(self._state(self.bill), before)
        header = {row["field"]: row for row in result["header_changes"]}
        self.assertEqual(set(header), {"ref", "to_check"})
        self.assertEqual(header["ref"]["old"], "AI-EDIT-1")
        self.assertEqual(header["ref"]["new"], "AI-EDIT-NEW")
        self.assertEqual(
            {row["line_id"] for row in result["line_changes"]},
            set((self.line1 | self.line2).ids),
        )
        for row in result["line_changes"]:
            fields = {change["field"]: change for change in row["changes"]}
            self.assertEqual(set(fields), {"account_id", "tax_ids"})
            self.assertEqual(fields["tax_ids"]["new"], ["AI purchase VAT 10"])
        self.assertEqual(
            result["totals_before"], {"untaxed": 300.0, "tax": 63.0, "total": 363.0}
        )
        self.assertEqual(
            result["totals_after"], {"untaxed": 300.0, "tax": 30.0, "total": 330.0}
        )
        applied = self._apply(TOOL, **kwargs)
        self.assertEqual(applied["result"]["totals"], result["totals_after"])
        state = self._state(self.bill)
        self.assertEqual(state["ref"], "AI-EDIT-NEW")
        self.assertTrue(state["to_check"])
        self.assertEqual(state["accounts"], self.expense2)
        self.assertEqual(state["taxes"], self.tax10)
        self.assertAlmostEqual(state["total"], 330.0)
        self.assertEqual(self.bill.state, "draft")

    def test_partner_by_vat_brings_its_bank_account(self):
        kwargs = {"bill": "AI-EDIT-1", "partner_vat": "X0000000AI"}
        preview = self._preview(TOOL, **kwargs)
        header = {row["field"]: row for row in preview["result"]["header_changes"]}
        self.assertEqual(header["partner_id"]["new"], "AI Supplier B")
        self.assertEqual(header["partner_bank_id"]["new"], self.bank_b.display_name)
        self._apply(TOOL, **kwargs)
        self.bill.invalidate_recordset()
        self.assertEqual(self.bill.partner_id, self.supplier_b)
        self.assertEqual(self.bill.partner_bank_id, self.bank_b)
        self.assertAlmostEqual(self.bill.amount_total, 363.0)

    def test_bank_account_must_belong_to_the_partner(self):
        with self.assertRaisesRegex(UserError, "is not one of the accounts"):
            self._preview(TOOL, bill_id=self.bill.id, partner_bank="AI 0000 0001")
        preview = self._preview(
            TOOL,
            bill_id=self.bill.id,
            partner_id=self.supplier_b.id,
            partner_bank="ai0000-0001",
        )
        header = {row["field"]: row for row in preview["result"]["header_changes"]}
        self.assertEqual(header["partner_bank_id"]["new"], self.bank_b.display_name)

    def test_invoice_date_moves_the_dates_odoo_derives(self):
        term = self.env["account.payment.term"].create(
            {
                "name": "AI 30 days",
                "line_ids": [(0, 0, {"value": "balance", "days": 30})],
            }
        )
        bill = self._draft(
            self.supplier_a,
            [50],
            self.prop,
            "AI-EDIT-TERM",
            invoice_payment_term_id=term.id,
        )
        kwargs = {"bill_id": bill.id, "invoice_date": "2026-11-02"}
        preview = self._preview(TOOL, **kwargs)
        header = {row["field"]: row for row in preview["result"]["header_changes"]}
        self.assertEqual(header["invoice_date"]["new"], "2026-11-02")
        self.assertEqual(header["invoice_date_due"]["new"], "2026-12-02")
        self._apply(TOOL, **kwargs)
        bill.invalidate_recordset()
        self.assertEqual(str(bill.invoice_date), "2026-11-02")
        self.assertEqual(str(bill.invoice_date_due), "2026-12-02")
        if "date" in header:
            self.assertEqual(str(bill.date), header["date"]["new"])
        with self.assertRaisesRegex(UserError, "payment term"):
            self._preview(TOOL, bill_id=bill.id, invoice_date_due="2026-12-31")

    def test_quantity_and_price_only_on_one_line(self):
        kwargs = {"bill_id": self.bill.id, "lines": self.line2.ids, "price_unit": 150}
        preview = self._preview(TOOL, **kwargs)
        self.assertEqual(preview["result"]["totals_after"]["total"], 302.5)
        self._apply(TOOL, **kwargs)
        self.line2.invalidate_recordset()
        self.assertEqual(self.line2.price_unit, 150)
        with self.assertRaisesRegex(UserError, "single line"):
            self._preview(
                TOOL,
                bill_id=self.bill.id,
                lines=(self.line1 | self.line2).ids,
                quantity=2,
            )

    def test_confirmation_is_bound_to_the_preview(self):
        kwargs = {"bill_id": self.bill.id, "ref": "AI-EDIT-TOKEN"}
        token = self._preview(TOOL, **kwargs)["confirmation"]["confirmation_token"]
        for bad in ("garbage", None):
            with self.subTest(token=bad), self.assertRaises(UserError):
                self._run(TOOL, confirm=True, confirmation_token=bad, **kwargs)
        with self.assertRaisesRegex(UserError, "does not match"):
            self._run(
                TOOL,
                confirm=True,
                confirmation_token=token,
                bill_id=self.bill.id,
                ref="AI-EDIT-OTHER",
            )
        # The bill changed after the preview.
        self.bill.ref = "AI-EDIT-ELSEWHERE"
        self.env.flush_all()
        with self.assertRaisesRegex(UserError, "does not match"):
            self._run(TOOL, confirm=True, confirmation_token=token, **kwargs)
        self.bill.invalidate_recordset()
        self.assertEqual(self.bill.ref, "AI-EDIT-ELSEWHERE")

    def test_refusals(self):
        posted = self._draft(self.supplier_a, [10], self.prop, "AI-EDIT-POSTED")
        posted.action_post()
        foreign = self._draft(self.supplier_a, [10], self.other_prop, "AI-EDIT-FAR")
        cases = [
            ({"bill_id": posted.id, "ref": "X"}, "not in draft"),
            ({"bill_id": foreign.id, "ref": "X"}, "was found"),
            ({"bill_id": self.bill.id, "ref": "AI-EDIT-1"}, "Nothing to change"),
            ({"bill_id": self.bill.id, "account": "629902"}, "Give the lines"),
            (
                {"bill_id": self.bill.id, "lines": [1] * 21, "account": "629902"},
                "at most 20",
            ),
            (
                {"bill_id": self.bill.id, "lines": self.line1.ids, "account": "AI000"},
                "No active account",
            ),
            (
                {
                    "bill_id": self.bill.id,
                    "lines": [posted.line_ids[0].id],
                    "label": "X",
                },
                "not product lines",
            ),
            ({"bill_id": self.bill.id, "payment_mode": "AI none"}, "(?i)payment.mode"),
        ]
        for kwargs, message in cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaisesRegex(UserError, message):
                    self._preview(TOOL, **kwargs)
        # The other hotel's bill is visible to a user of both hotels.
        self._preview(TOOL, user=self.reader_both, bill_id=foreign.id, ref="X")
        # A bill posted after the preview is refused on confirmation.
        kwargs = {"bill_id": self.bill.id, "ref": "AI-EDIT-LATE"}
        token = self._preview(TOOL, **kwargs)["confirmation"]["confirmation_token"]
        self.bill.action_post()
        with self.assertRaisesRegex(UserError, "not in draft"):
            self._run(TOOL, confirm=True, confirmation_token=token, **kwargs)
        self.assertEqual(self.bill.ref, "AI-EDIT-1")

    def test_only_allowed_users(self):
        with self.assertRaises(AccessError):
            self._preview(TOOL, user=self.pms_only, bill_id=self.bill.id, ref="X")
