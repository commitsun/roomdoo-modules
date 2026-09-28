import datetime

from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon

TODAY = datetime.date(2026, 9, 28)


@tagged("post_install", "-at_install")
class TestDownpaymentTransferBlocks(AccountTestInvoicingCommon):
    """The accounting building blocks, tested without a folio: the amount that
    gets moved and the accounts it is moved between are where a mistake would
    silently falsify two customers' balances."""

    @classmethod
    def setUpClass(cls, chart_template_ref=None):
        super().setUpClass(chart_template_ref=chart_template_ref)
        cls.company = cls.company_data["company"]

    def _invoice(self, partner, amount, post=True, date=TODAY, term=None):
        """Created without a payment term unless one is asked for: an
        instalment term splits the customer side into several lines, which is a
        different scenario with its own test."""
        invoice = self.init_invoice(
            "out_invoice",
            partner=partner,
            invoice_date=date,
            post=False,
            amounts=[amount],
            taxes=[],
        )
        invoice.invoice_payment_term_id = term
        if post:
            invoice.action_post()
        return invoice

    def _pay(self, invoice, amount):
        payment = self.env["account.payment"].create(
            {
                "payment_type": "inbound",
                "partner_type": "customer",
                "partner_id": invoice.partner_id.id,
                "amount": amount,
                "date": invoice.date,
                "journal_id": self.company_data["default_journal_bank"].id,
            }
        )
        payment.action_post()
        lines = (invoice.line_ids + payment.move_id.line_ids).filtered(
            lambda line: (
                line.account_id.account_type == "asset_receivable"
                and not line.reconciled
            )
        )
        lines.reconcile()
        return payment

    # ------------------------------------------------------------------
    def test_amount_is_what_was_collected_not_what_was_invoiced(self):
        """A down payment of 100 that only collected 60 must move 60. Moving
        100 would leave a phantom 40 owed by one customer and credit 40 nobody
        paid to the other."""
        invoice = self._invoice(self.partner_a, 100.0)
        self._pay(invoice, 60.0)
        self.assertEqual(invoice._downpayment_reconciled_amount(), 60.0)

    def test_amount_is_zero_when_nothing_was_collected(self):
        invoice = self._invoice(self.partner_a, 100.0)
        self.assertEqual(invoice._downpayment_reconciled_amount(), 0.0)

    def test_receivable_line_is_empty_when_there_is_more_than_one(self):
        """With instalment terms there are several customer lines and which
        balance to move stops being obvious, so the case is handed over."""
        invoice = self._invoice(
            self.partner_a,
            100.0,
            term=self.env.ref("account.account_payment_term_advance_60days"),
        )
        lines = invoice.line_ids.filtered(
            lambda line: line.account_id.account_type == "asset_receivable"
        )
        self.assertGreater(len(lines), 1, "precondition: only one customer line")
        self.assertFalse(invoice._receivable_line_for_transfer())

    def test_transfer_entry_uses_the_accounts_of_the_real_journal_items(self):
        """Never the partner's property account: if the down payment was issued
        on another account, reconcile() refuses with 'Entries are not from the
        same account'."""
        # The down payment is issued and collected on one account, and the
        # partner's default is changed afterwards. Taking the account from the
        # partner would then pick the wrong one and reconcile() would refuse
        # with "Entries are not from the same account".
        other_account = self.company_data["default_account_receivable"].copy()
        default_account = self.partner_a.property_account_receivable_id
        self.partner_a.property_account_receivable_id = other_account
        downpayment = self._invoice(self.partner_a, 100.0)
        self._pay(downpayment, 100.0)
        self.partner_a.property_account_receivable_id = default_account
        final = self._invoice(self.partner_b, 250.0)

        self.assertEqual(
            downpayment._receivable_line_for_transfer().account_id, other_account
        )
        self.assertNotEqual(
            self.partner_a.property_account_receivable_id,
            other_account,
            "precondition: the partner still points at the invoiced account",
        )

        transfer = final._create_transfer_entry(downpayment, 100.0, TODAY)
        debit = transfer.line_ids.filtered(lambda line: line.debit)
        credit = transfer.line_ids.filtered(lambda line: line.credit)
        self.assertEqual(debit.account_id, other_account)
        self.assertEqual(debit.partner_id, self.partner_a)
        target = final._receivable_line_for_transfer()
        self.assertEqual(credit.account_id, target.account_id)
        self.assertEqual(credit.partner_id, self.partner_b)
        self.assertEqual(debit.debit, 100.0)
        self.assertEqual(credit.credit, 100.0)
        self.assertEqual(transfer.state, "posted")

    def test_rectification_does_not_release_the_payment(self):
        """The whole point: cancel=False, so the payment stays applied to the
        down payment in the period it was issued in."""
        downpayment = self._invoice(self.partner_a, 100.0)
        self._pay(downpayment, 100.0)
        self.assertEqual(downpayment.payment_state, "paid")
        credit_note = downpayment._rectify_downpayment(TODAY)
        self.assertEqual(credit_note.state, "posted")
        self.assertEqual(credit_note.date, TODAY)
        self.assertEqual(downpayment.payment_state, "paid")
        self.assertTrue(downpayment._receivable_line_for_transfer().reconciled)

    def test_chronology_is_checked_before_posting(self):
        journal = self.company_data["default_journal_sale"]
        journal.check_chronology = True
        journal.refund_sequence = True
        older = self.init_invoice(
            "out_refund",
            partner=self.partner_a,
            invoice_date=datetime.date(2026, 1, 15),
            amounts=[10.0],
            taxes=[],
        )
        self.assertEqual(older.state, "draft")
        final = self._invoice(self.partner_b, 250.0)
        with self.assertRaises(UserError):
            final._check_transfer_chronology(journal, TODAY)

    def test_chronology_ignores_draft_invoices_with_refund_sequence(self):
        """With refund_sequence the constraint only compares refunds, which is
        what keeps a pile of draft invoices from blocking every transfer."""
        journal = self.company_data["default_journal_sale"]
        journal.check_chronology = True
        journal.refund_sequence = True
        # The invoice is posted first: the constraint would otherwise block the
        # fixture itself, which is the very behaviour being isolated here.
        final = self._invoice(self.partner_b, 250.0)
        older = self.init_invoice(
            "out_invoice",
            partner=self.partner_a,
            invoice_date=datetime.date(2026, 1, 15),
            amounts=[10.0],
            taxes=[],
        )
        self.assertEqual(older.state, "draft")
        final._check_transfer_chronology(journal, TODAY)
