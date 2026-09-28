from odoo.tests import tagged

from odoo.addons.queue_job.tests.common import trap_jobs

from .common import DownpaymentCase


@tagged("post_install", "-at_install")
class TestDownpaymentTransferEndToEnd(DownpaymentCase):
    """B3: a down payment collected from the anonymous customer and a final
    invoice issued to the guest."""

    def test_final_invoice_to_another_customer_is_marked_and_transferred(self):
        folio = self._folio()
        downpayment, _line = self._downpayment(folio, self.anonymous, 50.0)
        payment = self._collect(downpayment)
        self.assertEqual(downpayment.payment_state, "paid")

        final = self._final_invoice(folio, self.guest, 200.0)
        with trap_jobs() as trap:
            final.action_post()
            # Posting only marks and enqueues: the accounting work must never
            # ride inside _post().
            trap.assert_jobs_count(1)

        self.assertEqual(final.downpayment_transfer_state, "pending")
        self.assertIn(downpayment, final._get_downpayments_to_transfer())

        final._transfer_downpayments()
        self.assertEqual(final.downpayment_transfer_state, "done")

        # The payment never moved: that is the whole point.
        self.assertEqual(downpayment.payment_state, "paid")
        self.assertTrue(payment.is_reconciled)

        credit_note = self.env["account.move"].search(
            [("reversed_entry_id", "=", downpayment.id)]
        )
        self.assertEqual(len(credit_note), 1)
        self.assertEqual(credit_note.state, "posted")

        transfer = self.env["account.move"].search(
            [("move_type", "=", "entry"), ("ref", "like", downpayment.name)]
        )
        self.assertEqual(len(transfer), 1)
        self.assertEqual(sum(transfer.line_ids.mapped("debit")), 50.0)

        # The anonymous customer is left at zero for this operation.
        self.assertFalse(
            credit_note._receivable_line_for_transfer().filtered(
                lambda line: not line.reconciled
            )
        )

    def test_same_customer_needs_no_transfer(self):
        """B1: when the down payment was already issued to the guest, pms
        deducts it in the final invoice and there is nothing to move."""
        folio = self._folio()
        downpayment, _line = self._downpayment(folio, self.guest, 50.0)
        self._collect(downpayment)
        final = self._final_invoice(folio, self.guest, 200.0)
        with trap_jobs() as trap:
            final.action_post()
            trap.assert_jobs_count(0)
        self.assertFalse(final.downpayment_transfer_state)
        self.assertFalse(final._get_downpayments_to_transfer())

    def test_identified_third_party_is_left_for_accounting(self):
        """Art. 89.Cinco: rectifying VAT charged to a real customer without
        refunding them is not the same operation as exchanging a simplified
        invoice, so it is flagged instead of automated."""
        folio = self._folio()
        third_party = self.env["res.partner"].create({"name": "Real company SL"})
        downpayment, _line = self._downpayment(folio, third_party, 50.0)
        self._collect(downpayment)
        final = self._final_invoice(folio, self.guest, 200.0)
        with trap_jobs():
            final.action_post()
        final._transfer_downpayments()
        self.assertEqual(final.downpayment_transfer_state, "manual")
        self.assertIn("identified third", final.downpayment_transfer_message)
