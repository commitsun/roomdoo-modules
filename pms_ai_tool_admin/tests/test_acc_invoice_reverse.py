from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.pms_ai_tool.tools import PARAM_WRITE_ENABLED

from .common import AdminToolsMixin

TOOL = "acc_invoice_reverse"
REASON = "Wrong price agreed with the customer"


@tagged("post_install", "-at_install")
class TestAccInvoiceReverse(AdminToolsMixin, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_admin()
        cls.tool = cls.env.ref(f"pms_ai_tool_admin.ai_tool_{TOOL}")
        cls.tool.write(
            {
                "allowed_user_ids": [
                    (6, 0, (cls.reader | cls.reader_both | cls.pms_only).ids)
                ],
                "write_enabled": True,
            }
        )
        cls.env["ir.config_parameter"].sudo().set_param(PARAM_WRITE_ENABLED, "True")
        Partner = cls.env["res.partner"]
        cls.customer = Partner.create({"name": "AI Customer A", "is_company": True})
        cls.new_customer = Partner.create({"name": "AI Customer B", "is_company": True})
        cls.supplier = Partner.create({"name": "AI Supplier R", "is_company": True})
        cls.today = fields.Date.context_today(cls.env.user)
        cls.invoice = cls._invoice(cls.customer, 100, cls.prop)
        cls.other_invoice = cls._invoice(cls.customer, 70, cls.other_prop)
        cls.draft = cls._invoice(cls.customer, 40, cls.prop, post=False)
        cls.bill = cls._invoice(cls.supplier, 250, cls.prop, move_type="in_invoice")

    @classmethod
    def _invoice(cls, partner, price, prop, post=True, move_type="out_invoice"):
        day = cls.today - timedelta(days=10)
        move = cls.env["account.move"].create(
            {
                "move_type": move_type,
                "partner_id": partner.id,
                "invoice_date": day,
                "date": day,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "AI service",
                            "quantity": 1,
                            "price_unit": price,
                            "tax_ids": [(6, 0, [])],
                        },
                    )
                ],
            }
        )
        if post:
            move.action_post()
        # The property of the invoice and of its lines, as the PMS sets them.
        cls.env.cr.execute(
            "UPDATE account_move SET pms_property_id = %s WHERE id = %s",
            [prop.id, move.id],
        )
        cls.env.cr.execute(
            "UPDATE account_move_line SET pms_property_id = %s WHERE move_id = %s",
            [prop.id, move.id],
        )
        cls.env.invalidate_all()
        return move

    def _call(self, user=None, **kwargs):
        return self.tool.with_user(user or self.reader)._execute_tool(**kwargs)

    def _preview(self, user=None, **kwargs):
        kwargs.setdefault("invoice", self.invoice.name)
        kwargs.setdefault("reason", REASON)
        return self._call(user=user, **kwargs)

    def _confirm(self, preview, user=None, **kwargs):
        kwargs.setdefault("invoice", self.invoice.name)
        kwargs.setdefault("reason", REASON)
        token = preview["confirmation"]["confirmation_token"]
        return self._call(user=user, confirm=True, confirmation_token=token, **kwargs)

    def _credit_notes(self, move=None):
        return self.env["account.move"].search(
            [("reversed_entry_id", "=", (move or self.invoice).id)]
        )

    def _moves_count(self):
        return self.env["account.move"].search_count([])

    def test_tool_record_is_a_write_tool(self):
        self.assertEqual(self.tool.guard_mode, "write")
        annotations = self.tool._get_tool_definition()["annotations"]
        self.assertFalse(annotations["readOnlyHint"])
        self.assertTrue(annotations["destructiveHint"])
        self.assertTrue(self.tool.description.startswith("SENSITIVE"))

    def test_refund_preview_changes_nothing_then_draft_credit_note(self):
        moves_before = self._moves_count()
        preview = self._preview(mode="refund")
        self.assertEqual(self._moves_count(), moves_before)
        self.assertFalse(self._credit_notes())
        self.assertTrue(preview["confirmation"]["required"])
        result = preview["result"]
        self.assertEqual(result["invoice"]["id"], self.invoice.id)
        self.assertEqual(result["invoice"]["total"], 100.0)
        self.assertEqual(result["invoice"]["property"], self.prop.name)
        credit = result["credit_note"]
        self.assertEqual(
            (credit["type"], credit["state"], credit["total"]),
            ("out_refund", "draft", 100.0),
        )
        self.assertEqual(credit["reconciled_with_invoice"], 0.0)
        self.assertEqual(result["new_draft_invoice"], {})
        self.assertIn(self.invoice.name, preview["summary"])

        applied = self._confirm(preview, mode="refund")

        credit_note = self._credit_notes()
        self.assertEqual(len(credit_note), 1)
        self.assertEqual(self._moves_count(), moves_before + 1)
        self.assertEqual(credit_note.state, "draft")
        self.assertEqual(credit_note.move_type, "out_refund")
        self.assertEqual(credit_note.amount_total, 100.0)
        self.assertIn(REASON, credit_note.ref)
        self.assertEqual(credit_note.create_uid, self.reader)
        self.assertEqual(applied["result"]["credit_note"]["id"], credit_note.id)
        self.assertEqual(applied["result"]["credit_note"]["state"], "draft")
        self.assertEqual(self.invoice.amount_residual, 100.0)

    def test_cancel_posts_and_reconciles(self):
        preview = self._preview(mode="cancel")
        credit = preview["result"]["credit_note"]
        self.assertEqual(
            (credit["state"], credit["reconciled_with_invoice"]), ("posted", 100.0)
        )
        applied = self._confirm(preview, mode="cancel")
        credit_note = self._credit_notes()
        self.assertEqual(len(credit_note), 1)
        self.assertEqual(credit_note.state, "posted")
        self.assertEqual(credit_note.amount_total, credit["total"])
        self.assertEqual(credit_note.amount_residual, 0.0)
        self.assertEqual(self.invoice.amount_residual, 0.0)
        self.assertEqual(applied["result"]["invoice"]["pending"], 0.0)
        self.assertEqual(applied["result"]["credit_note"]["state"], "posted")
        # Nothing is pending any more: only a draft refund is possible now.
        with self.assertRaisesRegex(UserError, "already paid or credited"):
            self._preview(mode="cancel")
        warnings = " ".join(self._preview(mode="refund")["warnings"])
        self.assertIn("already has credit notes", warnings)

    def test_modify_with_new_partner_changes_the_customer(self):
        preview = self._preview(mode="modify", new_partner_id=self.new_customer.id)
        new_draft = preview["result"]["new_draft_invoice"]
        self.assertEqual(new_draft["partner_id"], self.new_customer.id)
        self.assertTrue(new_draft["partner_changed"])
        self.assertEqual(new_draft["state"], "draft")
        moves_before = self._moves_count()

        applied = self._confirm(
            preview, mode="modify", new_partner_id=self.new_customer.id
        )

        self.assertEqual(self._moves_count(), moves_before + 2)
        credit_note = self._credit_notes()
        self.assertEqual(credit_note.state, "posted")
        self.assertEqual(self.invoice.amount_residual, 0.0)
        result = applied["result"]["new_draft_invoice"]
        new_invoice = self.env["account.move"].browse(result["id"])
        self.assertEqual(new_invoice.state, "draft")
        self.assertEqual(new_invoice.move_type, "out_invoice")
        self.assertEqual(new_invoice.partner_id, self.new_customer)
        self.assertEqual(new_invoice.amount_total, 100.0)
        receivable = new_invoice.line_ids.filtered(
            lambda line: line.display_type == "payment_term"
        )
        self.assertEqual(receivable.partner_id, self.new_customer)
        # The original invoice keeps its customer.
        self.assertEqual(self.invoice.partner_id, self.customer)

    def test_vendor_bill_by_id(self):
        preview = self._preview(invoice=str(self.bill.id), mode="cancel")
        self.assertEqual(preview["result"]["credit_note"]["type"], "in_refund")
        self._confirm(preview, invoice=str(self.bill.id), mode="cancel")
        credit_note = self._credit_notes(self.bill)
        self.assertEqual(credit_note.move_type, "in_refund")
        self.assertEqual(self.bill.amount_residual, 0.0)

    def test_token_is_bound_to_the_arguments(self):
        preview = self._preview(mode="refund")
        with self.assertRaisesRegex(UserError, "does not match"):
            self._confirm(preview, mode="cancel")
        with self.assertRaisesRegex(UserError, "does not match"):
            self._confirm(preview, mode="refund", reason="Another reason")
        self.assertFalse(self._credit_notes())
        self._confirm(preview, mode="refund")
        with self.assertRaisesRegex(UserError, "already used"):
            self._confirm(preview, mode="refund")
        self.assertEqual(len(self._credit_notes()), 1)

    def test_refusals(self):
        with self.assertRaisesRegex(UserError, "not posted"):
            self._preview(invoice=str(self.draft.id), mode="refund")
        # Out of the user's properties.
        with self.assertRaisesRegex(UserError, "No customer invoice"):
            self._preview(invoice=str(self.other_invoice.id), mode="refund")
        both = self._preview(
            user=self.reader_both, invoice=str(self.other_invoice.id), mode="refund"
        )
        self.assertEqual(both["result"]["invoice"]["id"], self.other_invoice.id)
        with self.assertRaises(AccessError):
            self._preview(user=self.pms_only, mode="refund")
        with self.assertRaisesRegex(UserError, "future"):
            self._preview(mode="refund", date=str(self.today + timedelta(days=1)))
        with self.assertRaisesRegex(UserError, "earlier than the invoice"):
            self._preview(mode="refund", date=str(self.today - timedelta(days=30)))
        with self.assertRaisesRegex(UserError, "only accepted with mode=modify"):
            self._preview(mode="cancel", new_partner_id=self.new_customer.id)
        with self.assertRaisesRegex(UserError, "supplier_refund_number"):
            self._preview(mode="refund", supplier_refund_number="CN-1")
        with self.assertRaises(UserError):
            self._preview(mode="partial")
        self.assertFalse(self._credit_notes())

    def test_paid_invoice_only_allows_refund(self):
        journal = self.env["account.journal"].search(
            [("type", "=", "bank"), ("company_id", "=", self.env.company.id)],
            limit=1,
        )
        payment = self.env["account.payment"].create(
            {
                "payment_type": "inbound",
                "partner_type": "customer",
                "partner_id": self.customer.id,
                "amount": 40,
                "journal_id": journal.id,
                "date": self.today,
            }
        )
        payment.action_post()
        (self.invoice.line_ids + payment.move_id.line_ids).filtered(
            lambda line: line.account_id.account_type == "asset_receivable"
        ).reconcile()
        preview = self._preview(mode="cancel")
        self.assertIn("partly paid", " ".join(preview["warnings"]))
        self.assertEqual(
            preview["result"]["credit_note"]["reconciled_with_invoice"], 60.0
        )
