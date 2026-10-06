import datetime
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon

INVOICE_DATE = datetime.date(2026, 9, 20)


@tagged("post_install", "-at_install")
class TestRectificationKeepsReconciliation(AccountTestInvoicingCommon):
    """Rectifying an invoice that is being replaced, without unreconciling the
    payment it was settled with."""

    @classmethod
    def setUpClass(cls, chart_template_ref=None):
        super().setUpClass(chart_template_ref=chart_template_ref)
        cls.company = cls.company_data["company"]
        cls.transfer_journal = cls.company_data["default_journal_misc"]

    def _opt_in(self):
        """The company has a transfer journal, as a module would configure."""
        return patch.object(
            type(self.env["res.company"]),
            "_get_rectification_transfer_journal",
            lambda company: self.transfer_journal,
        )

    def _invoice(self, partner, amount):
        invoice = self.init_invoice(
            "out_invoice",
            partner=partner,
            invoice_date=INVOICE_DATE,
            post=False,
            amounts=[amount],
            taxes=[],
        )
        invoice.invoice_payment_term_id = False
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
        (invoice.line_ids + payment.move_id.line_ids).filtered(
            lambda line: line.account_id.account_type == "asset_receivable"
            and not line.reconciled
        ).reconcile()
        return payment

    @staticmethod
    def _replacement(original, partner):
        """A draft copy, the way the APIs build the invoice that replaces it."""
        return original.copy({"partner_id": partner.id})

    def _credit_note(self, original):
        return self.env["account.move"].search(
            [("reversed_entry_id", "=", original.id)]
        )

    # ------------------------------------------------------------------
    # when the core way is kept
    # ------------------------------------------------------------------
    def test_without_a_transfer_journal_the_core_way_is_kept(self):
        """No journal means the company did not opt in: nothing changes."""
        original = self._invoice(self.partner_a, 100.0)
        payment = self._pay(original, 100.0)
        new = self._replacement(original, self.partner_a)

        original._reissue_invoice(new)

        self.assertEqual(original.payment_state, "reversed")
        self.assertFalse(payment.is_reconciled)
        self.assertEqual(new.state, "posted")

    def test_an_unpaid_invoice_is_reversed_the_core_way(self):
        """Nothing is reconciled, so there is nothing to keep."""
        original = self._invoice(self.partner_a, 100.0)
        new = self._replacement(original, self.partner_b)

        with self._opt_in():
            original._reissue_invoice(new)

        self.assertEqual(original.payment_state, "reversed")
        self.assertEqual(new.state, "posted")
        self.assertFalse(
            self.env["account.move"].search(
                [("journal_id", "=", self.transfer_journal.id)]
            )
        )

    # ------------------------------------------------------------------
    # keeping the reconciliation
    # ------------------------------------------------------------------
    def test_same_customer_keeps_the_payment_and_gets_a_transfer_entry(self):
        """Even for the same customer the money goes through a transfer entry:
        reconciled directly with the credit note, the replacement would show as
        'reversed' while it is the live invoice of the stay."""
        original = self._invoice(self.partner_a, 100.0)
        payment = self._pay(original, 100.0)
        new = self._replacement(original, self.partner_a)

        with self._opt_in():
            credit_note = original._reissue_invoice(new)

        # The original never let go of its payment.
        self.assertEqual(original.payment_state, "paid")
        self.assertIn(
            original, payment.move_id.line_ids.matched_debit_ids.debit_move_id.move_id
        )
        self.assertEqual(credit_note.reversed_entry_id, original)
        self.assertEqual(credit_note.state, "posted")
        self.assertEqual(new.payment_state, "paid")
        self.assertTrue(original._is_rectified())
        self.assertFalse(new._is_rectified())
        transfer = self.env["account.move"].search(
            [("journal_id", "=", self.transfer_journal.id)]
        )
        self.assertEqual(transfer.line_ids.partner_id, self.partner_a)

    def test_another_customer_gets_the_balance_through_a_transfer_entry(self):
        original = self._invoice(self.partner_a, 100.0)
        payment = self._pay(original, 100.0)
        new = self._replacement(original, self.partner_b)

        with self._opt_in():
            original._reissue_invoice(new)

        self.assertEqual(original.payment_state, "paid")
        self.assertTrue(payment.is_reconciled)
        self.assertEqual(payment.move_id.line_ids.partner_id, self.partner_a)
        self.assertEqual(new.payment_state, "paid")
        transfer = self.env["account.move"].search(
            [("journal_id", "=", self.transfer_journal.id)]
        )
        self.assertEqual(len(transfer), 1)
        debit = transfer.line_ids.filtered("debit")
        credit = transfer.line_ids.filtered("credit")
        self.assertEqual(debit.partner_id, self.partner_a)
        self.assertEqual(credit.partner_id, self.partner_b)
        self.assertEqual(debit.debit, 100.0)
        self.assertTrue(debit.reconciled)
        self.assertTrue(credit.reconciled)

    def test_a_partly_paid_invoice_only_moves_what_was_collected(self):
        """The credit note first closes what the original still had open, so
        only the 60 collected reach the replacement, which owes the rest."""
        original = self._invoice(self.partner_a, 100.0)
        self._pay(original, 60.0)
        new = self._replacement(original, self.partner_a)

        with self._opt_in():
            credit_note = original._reissue_invoice(new)

        self.assertEqual(original.amount_residual, 0.0)
        self.assertEqual(credit_note.amount_residual, 0.0)
        self.assertEqual(new.amount_residual, 40.0)

    def test_a_replacement_that_bills_less_only_takes_what_it_owes(self):
        """The rest stays in favour of the customer who paid. Moved, it would
        be credited to the new customer, who paid nothing."""
        original = self._invoice(self.partner_a, 100.0)
        self._pay(original, 100.0)
        new = self.init_invoice(
            "out_invoice",
            partner=self.partner_b,
            invoice_date=INVOICE_DATE,
            post=False,
            amounts=[60.0],
            taxes=[],
        )
        new.invoice_payment_term_id = False

        with self._opt_in():
            credit_note = original._reissue_invoice(new)

        self.assertEqual(new.payment_state, "paid")
        self.assertEqual(credit_note.amount_residual, 40.0)
        self.assertEqual(credit_note.partner_id, self.partner_a)
        transfer = self.env["account.move"].search(
            [("journal_id", "=", self.transfer_journal.id)]
        )
        self.assertEqual(sum(transfer.line_ids.mapped("debit")), 60.0)

    def test_a_rectified_invoice_cannot_be_rectified_again(self):
        original = self._invoice(self.partner_a, 100.0)
        self._pay(original, 100.0)
        with self._opt_in():
            original._reissue_invoice(self._replacement(original, self.partner_a))
            with self.assertRaises(UserError):
                original._reissue_invoice(self._replacement(original, self.partner_a))

    def _spy_sii_refund_type(self):
        """Patch _reverse_moves to record the SII refund type it is called
        with. Returns the patcher and the list it fills."""
        move_class = type(self.env["account.move"])
        reverse_moves = move_class._reverse_moves
        seen = []

        def spy(moves, *args, **kwargs):
            seen.append(moves.env.context.get("sii_refund_type"))
            return reverse_moves(moves, *args, **kwargs)

        return patch.object(move_class, "_reverse_moves", spy), seen

    def test_the_credit_note_gets_the_sii_refund_type(self):
        """The credit note copies the invoice, whose sii_refund_type is empty,
        so the SII module only sets 'I' when the context carries it."""
        original = self._invoice(self.partner_a, 100.0)
        spy, seen = self._spy_sii_refund_type()
        with spy:
            original._rectify_keep_reconciliation(INVOICE_DATE)
        self.assertEqual(seen, ["I"])

    def test_the_core_way_also_gets_the_sii_refund_type(self):
        original = self._invoice(self.partner_a, 100.0)
        spy, seen = self._spy_sii_refund_type()
        with spy:
            original._reissue_invoice(self._replacement(original, self.partner_b))
        self.assertEqual(seen, ["I"])
        self.assertEqual(original.payment_state, "reversed")

    def test_a_partial_credit_note_is_left_open(self):
        """Its caller decides what it settles -- in pms_downpayment_lifecycle,
        the refund it gives back. Matched against what the original still owes,
        it would take that place."""
        original = self._invoice(self.partner_a, 100.0)
        self._pay(original, 60.0)
        credit_note = original._rectify_keep_reconciliation(INVOICE_DATE, amount=30.0)
        self.assertEqual(credit_note.amount_residual, 30.0)
        self.assertEqual(original.amount_residual, 40.0)

    def test_a_full_credit_note_settles_what_the_original_still_owes(self):
        original = self._invoice(self.partner_a, 100.0)
        self._pay(original, 60.0)
        credit_note = original._rectify_keep_reconciliation(INVOICE_DATE)
        self.assertEqual(original.amount_residual, 0.0)
        self.assertEqual(credit_note.amount_residual, 60.0)

    # ------------------------------------------------------------------
    # the payment of a rectified invoice
    # ------------------------------------------------------------------
    def test_the_payment_of_a_rectified_invoice_cannot_be_reset(self):
        """Reset, it would leave the rectified invoice owing what the
        replacement already received."""
        original = self._invoice(self.partner_a, 100.0)
        payment = self._pay(original, 100.0)
        with self._opt_in():
            original._reissue_invoice(self._replacement(original, self.partner_b))
        with self.assertRaisesRegex(UserError, "already been rectified"):
            payment.action_draft()
        self.assertEqual(payment.state, "posted")
        self.assertEqual(original.payment_state, "paid")

    def test_the_bypass_lets_the_payment_of_a_rectified_invoice_be_reset(self):
        original = self._invoice(self.partner_a, 100.0)
        payment = self._pay(original, 100.0)
        with self._opt_in():
            original._reissue_invoice(self._replacement(original, self.partner_b))
        payment.with_context(bypass_reconcile_lock_date=True).action_draft()
        self.assertEqual(payment.state, "draft")

    def test_the_payment_of_a_partly_rectified_invoice_can_be_reset(self):
        """Only a rectification that covers the invoice moves its money on."""
        original = self._invoice(self.partner_a, 100.0)
        payment = self._pay(original, 100.0)
        original._rectify_keep_reconciliation(INVOICE_DATE, amount=40.0)
        payment.action_draft()
        self.assertEqual(payment.state, "draft")

    def test_the_payment_of_a_live_invoice_can_be_reset(self):
        original = self._invoice(self.partner_a, 100.0)
        payment = self._pay(original, 100.0)
        payment.action_draft()
        self.assertEqual(payment.state, "draft")
        self.assertEqual(original.payment_state, "not_paid")

    # ------------------------------------------------------------------
    # _is_rectified
    # ------------------------------------------------------------------
    def test_is_rectified_reads_the_credit_notes_not_the_payment_state(self):
        original = self._invoice(self.partner_a, 100.0)
        self._pay(original, 100.0)
        self.assertFalse(original._is_rectified())
        original._rectify_keep_reconciliation(INVOICE_DATE, amount=40.0)
        self.assertFalse(original._is_rectified(), "a partial credit note")
        original._rectify_keep_reconciliation(INVOICE_DATE, amount=60.0)
        self.assertTrue(original._is_rectified())
        self.assertEqual(original.payment_state, "paid")

    def test_a_zero_invoice_without_credit_notes_is_not_rectified(self):
        original = self._invoice(self.partner_a, 0.0)
        self.assertFalse(original._is_rectified())
