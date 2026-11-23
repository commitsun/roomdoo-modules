from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, tagged

from .common import AdminToolsMixin


@tagged("post_install", "-at_install")
class TestPmsInvoiceLines(AdminToolsMixin, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_admin()
        cls.customer = cls.env["res.partner"].create({"name": "AI Lines Customer"})
        cls.invoice = cls._invoice(cls.prop, [("Night", 100, 2), ("Breakfast", 15, 1)])
        cls.foreign = cls._invoice(cls.other_prop, [("Night", 50, 1)])
        cls.credit = cls.invoice._reverse_moves(
            [{"invoice_date": "2026-06-20", "ref": "AI refund"}]
        )
        cls.credit.action_post()
        (cls.invoice + cls.credit).line_ids.filtered(
            lambda line: line.account_id.account_type == "asset_receivable"
        ).reconcile()
        for move, prop in (
            (cls.invoice, cls.prop),
            (cls.credit, cls.prop),
            (cls.foreign, cls.other_prop),
        ):
            cls.env.cr.execute(
                "UPDATE account_move SET pms_property_id = %s WHERE id = %s",
                [prop.id, move.id],
            )
        cls.env.invalidate_all()
        cls._pay(cls.foreign, 20)

    @classmethod
    def _invoice(cls, prop, lines):
        move = cls.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": cls.customer.id,
                "invoice_date": "2026-06-15",
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": name,
                            "quantity": qty,
                            "price_unit": price,
                            "tax_ids": [(6, 0, [])],
                        },
                    )
                    for name, price, qty in lines
                ],
            }
        )
        move.action_post()
        return move

    @classmethod
    def _pay(cls, invoice, value):
        journal = cls.env["account.journal"].search(
            [("type", "=", "bank"), ("company_id", "=", cls.env.company.id)], limit=1
        )
        payment = cls.env["account.payment"].create(
            {
                "payment_type": "inbound",
                "partner_type": "customer",
                "partner_id": invoice.partner_id.id,
                "amount": value,
                "journal_id": journal.id,
                "date": "2026-06-22",
            }
        )
        payment.action_post()
        (invoice.line_ids + payment.move_id.line_ids).filtered(
            lambda line: line.account_id.account_type == "asset_receivable"
        ).reconcile()

    def test_invoice_with_credit_note_link(self):
        response = self._run("pms_invoice_lines", number=self.invoice.name)
        self._assert_read_tool("pms_invoice_lines", response)
        invoice = response["result"]["invoices"][0]
        self.assertEqual(invoice["id"], self.invoice.id)
        self.assertEqual(
            [(line["description"], line["untaxed"]) for line in invoice["lines"]],
            [("Night", 200.0), ("Breakfast", 15.0)],
        )
        self.assertEqual(invoice["total"], 215.0)
        self.assertIsNone(invoice["reverses"])
        self.assertEqual(
            [link["id"] for link in invoice["reversed_by"]], [self.credit.id]
        )
        # The credit note is applied to the invoice: it is a settlement.
        kinds = [row["kind"] for row in invoice["applied_payments"]]
        self.assertEqual(kinds, ["credit_note"])

    def test_credit_note_points_to_invoice(self):
        response = self._run("pms_invoice_lines", move_id=self.credit.id)
        credit = response["result"]["invoices"][0]
        self.assertEqual(credit["reverses"]["id"], self.invoice.id)
        self.assertEqual(credit["reverses"]["number"], self.invoice.name)
        self.assertEqual(credit["type"], "out_refund")

    def test_payment_is_listed(self):
        reader = self.reader_both
        response = self._run("pms_invoice_lines", user=reader, move_id=self.foreign.id)
        row = response["result"]["invoices"][0]["applied_payments"][0]
        self.assertEqual(row["kind"], "payment")
        self.assertEqual(row["amount"], 20.0)
        self.assertEqual(str(row["date"]), "2026-06-22")
        self.assertEqual(row["journal_type"], "bank")
        self.assertTrue(row["payment_method"])
        self.assertEqual(response["result"]["invoices"][0]["amount_due"], 30.0)

    def test_scope_and_errors(self):
        with self.assertRaises(UserError):
            self._run("pms_invoice_lines", move_id=self.foreign.id)
        with self.assertRaises(UserError):
            self._run("pms_invoice_lines")
        with self.assertRaises(UserError):
            self._run("pms_invoice_lines", number="NOPE/0000")

    def test_needs_accounting_access(self):
        with self.assertRaises(AccessError):
            self._run("pms_invoice_lines", user=self.pms_only, move_id=self.invoice.id)
