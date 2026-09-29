from odoo.tests import tagged

from odoo.addons.queue_job.tests.common import trap_jobs

from .common import DownpaymentCase


@tagged("post_install", "-at_install")
class TestDownpaymentLifecycle(DownpaymentCase):
    """C1, C2 and C3: what has to happen to a down payment invoice when the
    payment behind it is refunded, cancelled or changed."""

    def _refund(self, folio, partner, amount):
        payment = self.env["account.payment"].create(
            {
                "payment_type": "outbound",
                "partner_type": "customer",
                "partner_id": partner.id,
                "amount": amount,
                "journal_id": self.bank_journal.id,
                "folio_ids": [(6, 0, folio.ids)],
            }
        )
        return payment

    # ------------------------------------------------------------------
    # C3 -- the amount of a payment already invoiced as a down payment changes
    # ------------------------------------------------------------------
    def test_pms_destructive_branch_is_disabled(self):
        """pms reverses the down payment with cancel=True, which unreconciles
        the payment and can rewrite a closed period, and never re-issues. The
        single question it asks first must now always answer no."""
        folio = self._folio()
        downpayment, _line = self._downpayment(folio, self.anonymous, 50.0)
        payment = self._collect(downpayment)
        self.assertTrue(payment.reconciled_invoice_ids)
        self.assertFalse(
            self.env["account.payment"]._check_has_downpayment_invoice(payment)
        )

    def test_reset_to_draft_rectifies_instead_of_destroying(self):
        folio = self._folio()
        downpayment, _line = self._downpayment(folio, self.anonymous, 50.0)
        payment = self._collect(downpayment)
        self.assertEqual(downpayment.payment_state, "paid")

        payment.action_draft()

        credit_note = self.env["account.move"].search(
            [("reversed_entry_id", "=", downpayment.id)]
        )
        self.assertEqual(len(credit_note), 1)
        self.assertEqual(credit_note.state, "posted")
        self.assertEqual(credit_note.amount_total, 50.0)
        # The invoice still exists and is not in the air: its own credit note
        # settles it, so neither shows a debt that is no longer real.
        self.assertEqual(downpayment.state, "posted")
        self.assertEqual(downpayment.amount_residual, 0.0)
        self.assertEqual(credit_note.amount_residual, 0.0)
        self.assertEqual(payment.downpayment_reissue_partner_id, self.anonymous)

    def test_reissue_after_the_amount_changed(self):
        folio = self._folio()
        downpayment, _line = self._downpayment(folio, self.anonymous, 50.0)
        payment = self._collect(downpayment)

        payment.action_draft()
        payment.amount = 80.0
        payment.action_post()

        self.assertFalse(payment.downpayment_reissue_partner_id)
        reissued = payment.reconciled_invoice_ids
        self.assertEqual(len(reissued), 1)
        self.assertNotEqual(reissued, downpayment)
        self.assertTrue(reissued._is_downpayment())
        self.assertEqual(reissued.amount_total, 80.0)
        self.assertEqual(reissued.partner_id, self.anonymous)
        self.assertEqual(reissued.payment_state, "paid")

    def test_cancelling_the_payment_does_not_reissue(self):
        folio = self._folio()
        downpayment, _line = self._downpayment(folio, self.anonymous, 50.0)
        payment = self._collect(downpayment)

        payment.action_draft()
        payment.action_cancel()

        self.assertFalse(payment.downpayment_reissue_partner_id)
        self.assertEqual(downpayment._amount_open_to_rectify(), 0.0)

    # ------------------------------------------------------------------
    # C1 -- a refund rectifies the down payment it gives back
    # ------------------------------------------------------------------
    def test_refund_rectifies_the_down_payment_partially(self):
        tax = self._tax(10.0)
        folio = self._folio()
        downpayment, _line = self._downpayment(folio, self.anonymous, 100.0, tax)
        self.assertEqual(downpayment.amount_total, 110.0)
        self._collect(downpayment)

        refund = self._refund(folio, self.anonymous, 33.0)
        with trap_jobs() as trap:
            refund.action_post()
            trap.assert_jobs_count(1)
        self.assertEqual(refund.downpayment_refund_state, "pending")

        refund._rectify_downpayments_for_refund()

        self.assertEqual(refund.downpayment_refund_state, "done")
        credit_note = self.env["account.move"].search(
            [("reversed_entry_id", "=", downpayment.id)]
        )
        self.assertEqual(len(credit_note), 1)
        # 33 is the gross figure. Scaling price_unit rather than setting it is
        # what keeps the credit note at 33 instead of 36.30.
        self.assertEqual(credit_note.amount_total, 33.0)
        self.assertEqual(credit_note.amount_tax, 3.0)
        self.assertEqual(downpayment._amount_open_to_rectify(), 77.0)
        # The money given back settles the credit note.
        self.assertEqual(credit_note.amount_residual, 0.0)
        self.assertTrue(refund.is_reconciled)

    def test_a_second_refund_cannot_rectify_the_same_euro_twice(self):
        folio = self._folio()
        downpayment, _line = self._downpayment(folio, self.anonymous, 100.0)
        self._collect(downpayment)

        first = self._refund(folio, self.anonymous, 60.0)
        with trap_jobs():
            first.action_post()
        first._rectify_downpayments_for_refund()

        second = self._refund(folio, self.anonymous, 60.0)
        with trap_jobs():
            second.action_post()
        second._rectify_downpayments_for_refund()

        self.assertEqual(downpayment._amount_open_to_rectify(), 0.0)
        # 60 + 60 was given back but only 100 was ever invoiced: the 20 that
        # rectify nothing are flagged instead of being invented.
        self.assertEqual(second.downpayment_refund_state, "manual")
        rectified = self.env["account.move"].search(
            [("reversed_entry_id", "=", downpayment.id)]
        )
        self.assertEqual(sum(rectified.mapped("amount_total")), 100.0)

    def test_a_refund_with_no_down_payment_is_left_alone(self):
        folio = self._folio()
        refund = self._refund(folio, self.guest, 30.0)
        with trap_jobs() as trap:
            refund.action_post()
            trap.assert_jobs_count(0)
        self.assertFalse(refund.downpayment_refund_state)

    def test_a_credit_note_is_never_mistaken_for_a_down_payment(self):
        """`reconciled_invoice_ids` includes credit notes, and a credit note
        reversing a down payment answers True to `_is_downpayment()` because
        `folio_line_ids` is copied over by `_reverse_moves`. Read naively, the
        module rectifies its own rectification and the documents multiply."""
        folio = self._folio()
        downpayment, _line = self._downpayment(folio, self.anonymous, 100.0)
        self._collect(downpayment)
        refund = self._refund(folio, self.anonymous, 40.0)
        with trap_jobs():
            refund.action_post()
        refund._rectify_downpayments_for_refund()
        credit_note = self.env["account.move"].search(
            [("reversed_entry_id", "=", downpayment.id)]
        )
        self.assertEqual(len(credit_note), 1)
        self.assertTrue(
            credit_note._is_downpayment(),
            "the trap: it does look like a down payment",
        )
        self.assertIn(credit_note, refund.reconciled_invoice_ids)

        self.assertNotIn(credit_note, refund._downpayment_invoices())

        refund.action_draft()

        self.assertFalse(
            self.env["account.move"].search(
                [("reversed_entry_id", "=", credit_note.id)]
            ),
            "no counter-invoice may be issued against our own credit note",
        )
        self.assertFalse(refund.downpayment_reissue_partner_id)

    # ------------------------------------------------------------------
    # C2 -- cancelling the stay voids the down payment
    # ------------------------------------------------------------------
    def test_cancelling_the_folio_rectifies_the_down_payment_in_full(self):
        folio = self._folio()
        downpayment, _line = self._downpayment(folio, self.anonymous, 50.0)
        self._collect(downpayment)

        with trap_jobs() as trap:
            folio.action_cancel()
            # pms re-enters action_cancel from the last reservation, so the
            # folio is marked twice; the work itself is idempotent.
            self.assertGreaterEqual(len(trap.enqueued_jobs), 1)
        self.assertEqual(folio.downpayment_cancel_state, "pending")

        folio._rectify_downpayments_for_cancel()

        self.assertEqual(folio.downpayment_cancel_state, "done")
        credit_note = self.env["account.move"].search(
            [("reversed_entry_id", "=", downpayment.id)]
        )
        self.assertEqual(len(credit_note), 1)
        self.assertEqual(credit_note.amount_total, 50.0)
        # Left open on purpose: the stay will not be invoiced, so nothing of the
        # folio absorbs it. It stands in favour of the customer until the
        # penalty is billed or the money is given back.
        self.assertEqual(credit_note.amount_residual, 50.0)
        self.assertEqual(downpayment._amount_open_to_rectify(), 0.0)

        # Running it again finds nothing: the re-entrant call is harmless.
        folio._rectify_downpayments_for_cancel()
        self.assertEqual(
            len(
                self.env["account.move"].search(
                    [("reversed_entry_id", "=", downpayment.id)]
                )
            ),
            1,
        )

    def test_cancelling_after_a_partial_refund_rectifies_only_the_rest(self):
        folio = self._folio()
        downpayment, _line = self._downpayment(folio, self.anonymous, 100.0)
        self._collect(downpayment)

        refund = self._refund(folio, self.anonymous, 40.0)
        with trap_jobs():
            refund.action_post()
        refund._rectify_downpayments_for_refund()

        with trap_jobs():
            folio.action_cancel()
        folio._rectify_downpayments_for_cancel()

        rectified = self.env["account.move"].search(
            [("reversed_entry_id", "=", downpayment.id)]
        )
        self.assertEqual(len(rectified), 2)
        self.assertEqual(sum(rectified.mapped("amount_total")), 100.0)
        self.assertEqual(downpayment._amount_open_to_rectify(), 0.0)
