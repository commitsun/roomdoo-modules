from unittest.mock import patch

from odoo import fields
from odoo.tests import tagged

from odoo.addons.queue_job.tests.common import trap_jobs

from .common import DownpaymentCase


@tagged("post_install", "-at_install")
class TestDownpaymentTransferRecheck(DownpaymentCase):
    """The review list: invoices left in manual review or failed are looked at
    again with the data of the day, so that the list empties itself as the
    cases get sorted out."""

    def _marked_final_invoice(self):
        folio = self._folio()
        downpayment, _line = self._downpayment(folio, self.anonymous, 50.0)
        self._collect(downpayment)
        final = self._final_invoice_via_pms(folio, self.guest)
        with trap_jobs():
            final.action_post()
        self.assertEqual(final.downpayment_transfer_state, "pending")
        return final, downpayment

    def _left_for_review(self, final, message="left for review earlier"):
        """A verdict taken at posting time that no longer holds."""
        final.write(
            {
                "downpayment_transfer_state": "manual",
                "downpayment_transfer_message": message,
            }
        )

    def test_a_case_settled_since_leaves_the_list(self):
        final, downpayment = self._marked_final_invoice()
        self._left_for_review(final)
        downpayment._rectify_downpayment(fields.Date.today())

        with trap_jobs() as trap:
            final.action_recheck_downpayment_transfer()
            trap.assert_jobs_count(0)

        self.assertEqual(final.downpayment_transfer_state, "done")
        self.assertIn("Nothing left to transfer", final.downpayment_transfer_message)

    def test_a_case_now_clear_goes_back_to_the_transfer_job(self):
        final, downpayment = self._marked_final_invoice()
        self._left_for_review(final)

        with trap_jobs() as trap:
            final.action_recheck_downpayment_transfer()
            trap.assert_jobs_count(1)
            self.assertEqual(final.downpayment_transfer_state, "pending")
            trap.perform_enqueued_jobs()

        self.assertEqual(final.downpayment_transfer_state, "done")
        self.assertTrue(
            self.env["account.move"].search(
                [("reversed_entry_id", "=", downpayment.id)]
            )
        )

    def test_a_case_still_unclear_is_not_requeued_nor_repeated_in_chatter(self):
        folio = self._folio()
        downpayment, _line = self._downpayment(folio, self.anonymous, 50.0)
        self._collect(downpayment)
        # Bills less than the down payment standing on the folio.
        final = self._final_invoice(folio, self.guest, amount=30.0)
        with trap_jobs():
            final.action_post()
        final._transfer_downpayments()
        self.assertEqual(final.downpayment_transfer_state, "manual")
        reason = final.downpayment_transfer_message
        messages = len(final.message_ids)

        with trap_jobs() as trap:
            final.action_recheck_downpayment_transfer()
            final.action_recheck_downpayment_transfer()
            trap.assert_jobs_count(0)

        self.assertEqual(final.downpayment_transfer_state, "manual")
        self.assertEqual(final.downpayment_transfer_message, reason)
        self.assertEqual(len(final.message_ids), messages)
        self.assertFalse(
            self.env["account.move"].search(
                [("reversed_entry_id", "=", downpayment.id)]
            )
        )

    def test_an_identified_third_party_is_not_requeued(self):
        folio = self._folio()
        third_party = self.env["res.partner"].create({"name": "Real company SL"})
        downpayment, _line = self._downpayment(folio, third_party, 50.0)
        self._collect(downpayment)
        final = self._final_invoice_via_pms(folio, self.guest)
        with trap_jobs():
            final.action_post()
        final._transfer_downpayments()
        self.assertEqual(final.downpayment_transfer_state, "manual")
        messages = len(final.message_ids)

        with trap_jobs() as trap:
            final.action_recheck_downpayment_transfer()
            trap.assert_jobs_count(0)

        self.assertEqual(final.downpayment_transfer_state, "manual")
        self.assertIn("identified third", final.downpayment_transfer_message)
        self.assertEqual(len(final.message_ids), messages)

    def test_a_failed_one_is_retried(self):
        final, _downpayment = self._marked_final_invoice()
        final.write(
            {
                "downpayment_transfer_state": "failed",
                "downpayment_transfer_message": "could not obtain lock",
            }
        )

        with trap_jobs() as trap:
            final.action_recheck_downpayment_transfer()
            trap.assert_jobs_count(1)

        self.assertEqual(final.downpayment_transfer_state, "pending")

    def test_pending_and_done_are_left_alone(self):
        final, _downpayment = self._marked_final_invoice()
        with trap_jobs() as trap:
            final.action_recheck_downpayment_transfer()
            trap.assert_jobs_count(0)
        self.assertEqual(final.downpayment_transfer_state, "pending")

    def test_the_cron_goes_on_after_an_invoice_fails(self):
        # Two stays on the same nights need two rooms.
        self.env["pms.room"].create(
            {
                "pms_property_id": self.property.id,
                "name": "Transfer 102",
                "room_type_id": self.room_type.id,
                "capacity": 2,
            }
        )
        first, _dp1 = self._marked_final_invoice()
        second, _dp2 = self._marked_final_invoice()
        self._left_for_review(first)
        self._left_for_review(second)
        original = type(first)._recheck_downpayment_transfer

        def recheck(move):
            if move == first:
                raise ValueError("boom")
            return original(move)

        with patch.object(
            type(first),
            "_recheck_downpayment_transfer",
            autospec=True,
            side_effect=recheck,
        ), trap_jobs():
            self.env["account.move"]._cron_recheck_downpayment_transfers()

        self.assertEqual(first.downpayment_transfer_state, "manual")
        self.assertEqual(second.downpayment_transfer_state, "pending")
