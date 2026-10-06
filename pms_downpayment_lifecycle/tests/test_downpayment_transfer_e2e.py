from unittest.mock import patch

import psycopg2
from psycopg2 import errorcodes

from odoo import fields
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

        final = self._final_invoice_via_pms(folio, self.guest)
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

    def test_a_partly_refunded_down_payment_only_moves_what_is_left(self):
        """A down payment collected from the anonymous customer, part of it
        given back, and then the stay invoiced to someone else.

        The refund has already rectified its part, so what the transfer may
        rectify -- and move -- is only the remainder. Doing it for the full
        amount left the down payment with more credit notes than it was ever
        issued for, the down payment account off by the difference, and the new
        customer holding a balance the guest had already been paid back.
        """
        folio = self._folio()
        downpayment, _line = self._downpayment(folio, self.anonymous, 100.0)
        self._collect(downpayment)

        refund = self.env["account.payment"].create(
            {
                "payment_type": "outbound",
                "partner_type": "customer",
                "partner_id": self.anonymous.id,
                "amount": 60.0,
                "journal_id": self.bank_journal.id,
                "folio_ids": [(6, 0, folio.ids)],
            }
        )
        with trap_jobs() as trap:
            refund.action_post()
            trap.perform_enqueued_jobs()
        self.assertEqual(refund.downpayment_refund_state, "done")
        self.assertEqual(downpayment._amount_open_to_rectify(), 40.0)

        final = self._final_invoice_via_pms(folio, self.guest)
        with trap_jobs():
            final.action_post()
        final._transfer_downpayments()
        self.assertEqual(final.downpayment_transfer_state, "done")

        credit_notes = self.env["account.move"].search(
            [("reversed_entry_id", "=", downpayment.id)]
        )
        self.assertEqual(len(credit_notes), 2)
        self.assertEqual(
            sum(credit_notes.mapped("amount_total")),
            100.0,
            "a down payment can never be rectified for more than it was issued for",
        )
        self.assertEqual(downpayment._amount_open_to_rectify(), 0.0)

        transfer = self.env["account.move"].search(
            [("move_type", "=", "entry"), ("ref", "like", downpayment.name)]
        )
        self.assertEqual(len(transfer), 1)
        self.assertEqual(
            sum(transfer.line_ids.mapped("debit")),
            40.0,
            "only the part that was not given back may move to the new customer",
        )

    def test_same_customer_is_deducted_by_pms_and_left_alone(self):
        """The ordinary case: the down payment already existed when the invoice
        was built, so pms discounted it inside the invoice and there is nothing
        for this module to do. Rectifying on top would credit it twice."""
        folio = self._folio()
        downpayment, _line = self._downpayment(folio, self.guest, 50.0)
        self._collect(downpayment)
        final = self._final_invoice_via_pms(folio, self.guest)
        self.assertTrue(
            final.invoice_line_ids.folio_line_ids.filtered("is_downpayment"),
            "pms must have discounted it inside the invoice",
        )
        with trap_jobs() as trap:
            final.action_post()
            trap.assert_jobs_count(0)
        self.assertFalse(final.downpayment_transfer_state)

    def test_identified_third_party_is_left_for_accounting(self):
        """Art. 89.Cinco: rectifying VAT charged to a real customer without
        refunding them is not the same operation as exchanging a simplified
        invoice, so it is flagged instead of automated."""
        folio = self._folio()
        third_party = self.env["res.partner"].create({"name": "Real company SL"})
        downpayment, _line = self._downpayment(folio, third_party, 50.0)
        self._collect(downpayment)
        final = self._final_invoice_via_pms(folio, self.guest)
        with trap_jobs():
            final.action_post()
        final._transfer_downpayments()
        self.assertEqual(final.downpayment_transfer_state, "manual")
        self.assertIn("identified third", final.downpayment_transfer_message)

    def test_the_transfer_entry_carries_hotel_analytic_and_folio(self):
        """An entry with no property is the odd one out in the ledger, and
        without the property there is no analytic either: pms hangs the analytic
        distribution off pms_property_id. The folio number goes in the reference
        because the entry cannot be linked to the folio by relation."""
        folio = self._folio()
        downpayment, _line = self._downpayment(folio, self.anonymous, 50.0)
        self._collect(downpayment)
        final = self._final_invoice_via_pms(folio, self.guest)
        final.action_post()
        final._transfer_downpayments()

        entry = self.env["account.move"].search(
            [("move_type", "=", "entry"), ("ref", "like", downpayment.name)]
        )
        self.assertEqual(len(entry), 1)
        self.assertEqual(entry.pms_property_id, self.property)
        self.assertEqual(
            entry.line_ids.mapped("pms_property_id"),
            self.property,
            "every line must carry the hotel, not just the header",
        )
        self.assertIn(folio.name, entry.ref)
        for line in entry.line_ids:
            self.assertIn(folio.name, line.name)
        if self.property.analytic_account_id:
            for line in entry.line_ids:
                self.assertEqual(
                    line.analytic_distribution,
                    {str(self.property.analytic_account_id.id): 100.0},
                    "the analytic must follow from the property by itself",
                )

    # ------------------------------------------------------------------
    # job robustness
    # ------------------------------------------------------------------
    def _marked_final_invoice(self):
        folio = self._folio()
        downpayment, _line = self._downpayment(folio, self.anonymous, 50.0)
        self._collect(downpayment)
        final = self._final_invoice_via_pms(folio, self.guest)
        with trap_jobs():
            final.action_post()
        self.assertEqual(final.downpayment_transfer_state, "pending")
        return final, downpayment

    def test_a_lock_conflict_is_retried_not_recorded_as_a_failure(self):
        """Every entry of the company takes the same entry sequence, so a
        transfer can collide with any other posting. That is queue_job's to
        retry; recording it as 'failed' left it there for good. It goes up as
        it is: queue_job's runner retries concurrency errors with no limit,
        while a RetryableJobError raised inside the job counts against
        max_retries."""
        final, _downpayment = self._marked_final_invoice()

        class LockConflict(psycopg2.OperationalError):
            pgcode = errorcodes.LOCK_NOT_AVAILABLE

        def conflict(*args, **kwargs):
            raise LockConflict("could not obtain lock")

        with patch.object(type(final), "_run_downpayment_transfer", conflict):
            with self.assertRaises(LockConflict):
                final._transfer_downpayments()
        self.assertEqual(final.downpayment_transfer_state, "pending")

    def test_nothing_left_to_transfer_is_said_so(self):
        """Marked at posting, settled by something else before the job ran: the
        invoice says so instead of closing 'done' with an empty message, which
        looked exactly like a transfer that had been made."""
        final, downpayment = self._marked_final_invoice()
        downpayment._rectify_downpayment(fields.Date.today())

        final._transfer_downpayments()

        self.assertEqual(final.downpayment_transfer_state, "done")
        self.assertIn("Nothing left to transfer", final.downpayment_transfer_message)

    def test_without_a_transfer_journal_it_is_left_for_review(self):
        """Nowhere to post the transfer entry: the job says so instead of
        failing, and the down payment is not touched."""
        final, downpayment = self._marked_final_invoice()
        self.company.downpayment_transfer_journal_id = False

        final._transfer_downpayments()

        self.assertEqual(final.downpayment_transfer_state, "manual")
        self.assertIn("transfer journal", final.downpayment_transfer_message)
        self.assertFalse(
            self.env["account.move"].search(
                [("reversed_entry_id", "=", downpayment.id)]
            )
        )

    def test_a_down_payment_another_invoice_deducted_is_not_transferred(self):
        """A folio billed in two invoices: the first one deducted the down
        payment, so giving it to the second one would deduct it twice."""
        folio = self._folio()
        downpayment, line = self._downpayment(folio, self.anonymous, 50.0)
        self._collect(downpayment)
        # pms deducts it in the invoice of its own customer.
        first = self._final_invoice_via_pms(folio, self.anonymous)
        self.assertIn(line, first.invoice_line_ids.folio_line_ids)
        first.action_post()
        second = self._final_invoice(folio, self.guest)

        self.assertFalse(second._get_downpayments_to_transfer())

        # Once the first invoice is rectified its deduction no longer stands.
        first._rectify_keep_reconciliation(fields.Date.today())
        self.assertEqual(second._get_downpayments_to_transfer(), downpayment)

    def test_an_invoice_rectified_before_its_transfer_leaves_it_alone(self):
        """Edited from the app right after posting: the invoice that replaces
        it has its own transfer. Transferring to the rectified one would leave
        the money open on a document that no longer stands."""
        final, downpayment = self._marked_final_invoice()
        final._rectify_keep_reconciliation(fields.Date.today())

        final._transfer_downpayments()

        self.assertEqual(final.downpayment_transfer_state, "done")
        self.assertIn("Nothing transferred", final.downpayment_transfer_message)
        self.assertFalse(
            self.env["account.move"].search(
                [("reversed_entry_id", "=", downpayment.id)]
            )
        )
        self.assertEqual(downpayment._amount_open_to_rectify(), 50.0)
