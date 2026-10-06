from odoo.tests import tagged

from odoo.addons.queue_job.tests.common import trap_jobs

from .common import DownpaymentCase


@tagged("post_install", "-at_install")
class TestAutoinvoiceReversal(DownpaymentCase):
    """pms_autoinvoice reverses the down payments that a final invoice did not
    deduct with cancel=True, which unreconciles them from their payment: the
    payment is left loose and the final invoice unpaid. The anonymous ones that
    collected money are left to the transfer instead, whatever the period, and
    so is every one of a locked period."""

    def _reversals(self, downpayment):
        return self.env["account.move"].search(
            [("reversed_entry_id", "=", downpayment.id)]
        )

    def test_downpayment_of_a_locked_period_is_not_reversed(self):
        folio = self._folio()
        downpayment, _line = self._downpayment(folio, self.anonymous, 50.0)
        payment = self._collect(downpayment)
        self.company.period_lock_date = downpayment.date

        reversed_moves = downpayment._reverse_downpayment_invoices()

        self.assertFalse(reversed_moves)
        self.assertFalse(self._reversals(downpayment))
        # The closed month keeps its picture: the payment never moved.
        self.assertEqual(downpayment.payment_state, "paid")
        self.assertTrue(payment.is_reconciled)

    def test_downpayment_of_an_open_period_is_not_reversed_either(self):
        """Nothing closed is rewritten in an open period, but the payment would
        still be left loose and the final invoice unpaid."""
        folio = self._folio()
        downpayment, _line = self._downpayment(folio, self.anonymous, 50.0)
        payment = self._collect(downpayment)

        reversed_moves = downpayment._reverse_downpayment_invoices()

        self.assertFalse(reversed_moves)
        self.assertFalse(self._reversals(downpayment))
        self.assertTrue(payment.is_reconciled)

    def test_downpayment_that_collected_nothing_is_reversed(self):
        """No money to move: the usual reversal is the right document."""
        folio = self._folio()
        downpayment, _line = self._downpayment(folio, self.anonymous, 50.0)

        reversed_moves = downpayment._reverse_downpayment_invoices()

        self.assertEqual(reversed_moves, self._reversals(downpayment))
        self.assertEqual(len(reversed_moves), 1)
        self.assertEqual(downpayment.payment_state, "reversed")

    def test_a_named_customers_downpayment_is_still_reversed(self):
        """The transfer would only leave it for accounting, so in an open
        period it is reversed as it always was."""
        folio = self._folio()
        downpayment, _line = self._downpayment(folio, self.guest, 50.0)
        self._collect(downpayment)

        reversed_moves = downpayment._reverse_downpayment_invoices()

        self.assertEqual(len(reversed_moves), 1)

    def test_a_named_customers_downpayment_of_a_locked_period_is_not_reversed(self):
        folio = self._folio()
        downpayment, _line = self._downpayment(folio, self.guest, 50.0)
        payment = self._collect(downpayment)
        self.company.period_lock_date = downpayment.date

        self.assertFalse(downpayment._reverse_downpayment_invoices())
        self.assertTrue(payment.is_reconciled)

    def test_without_a_transfer_journal_it_is_reversed(self):
        """The transfer could not move the money either."""
        folio = self._folio()
        downpayment, _line = self._downpayment(folio, self.anonymous, 50.0)
        self._collect(downpayment)
        self.company.downpayment_transfer_journal_id = False

        reversed_moves = downpayment._reverse_downpayment_invoices()

        self.assertEqual(len(reversed_moves), 1)

    def test_the_final_invoice_gets_the_money_through_the_transfer(self):
        """The order of the nightly autoinvoice: the final invoice is posted,
        which queues the transfer, and the down payments it did not deduct are
        handed to the reversal right after. The money reaches the final invoice
        and the payment stays where it was."""
        folio = self._folio()
        downpayment, _line = self._downpayment(folio, self.anonymous, 50.0)
        payment = self._collect(downpayment)
        final = self._final_invoice_via_pms(folio, self.guest)
        with trap_jobs() as trap:
            final.action_post()
            downpayment._reverse_downpayment_invoices()
            trap.perform_enqueued_jobs()

        self.assertEqual(final.downpayment_transfer_state, "done")
        self.assertEqual(final.amount_residual, final.amount_total - 50.0)
        self.assertEqual(downpayment.payment_state, "paid")
        self.assertTrue(payment.is_reconciled)
        self.assertEqual(len(self._reversals(downpayment)), 1)
