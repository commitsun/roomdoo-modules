from odoo.tests import tagged

from .common import DownpaymentCase


@tagged("post_install", "-at_install")
class TestAutoinvoiceReversal(DownpaymentCase):
    """pms_autoinvoice reverses the down payments that a final invoice did not
    deduct with cancel=True, which unreconciles them from their payment. In a
    locked period they are left to the transfer instead."""

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

    def test_downpayment_of_an_open_period_is_still_reversed(self):
        folio = self._folio()
        downpayment, _line = self._downpayment(folio, self.anonymous, 50.0)
        self._collect(downpayment)

        reversed_moves = downpayment._reverse_downpayment_invoices()

        self.assertEqual(reversed_moves, self._reversals(downpayment))
        self.assertEqual(len(reversed_moves), 1)
