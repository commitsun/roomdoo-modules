"""A sync job that queue_job gives up on (retries exhausted on a transient
vendor error, or an unexpected error) must not leave its credential pending
forever. A credential that never reached the vendor is failed, so the
reconciliation issues a new one; one already on the locks is left to the
cancellation flow that revokes it."""

import uuid

from odoo.addons.queue_job.tests.common import trap_jobs

from .common import CommonSmartlock


class TestDeadSyncJob(CommonSmartlock):
    def setUp(self):
        super().setUp()
        self.reservation = self._create_reservation()

    def _code_with_job(self, **code_vals):
        code = self._plant_live_code(self.reservation, **code_vals)
        QueueJob = self.env["queue.job"].sudo()
        job = QueueJob.with_context(_job_edit_sentinel=QueueJob.EDIT_SENTINEL).create(
            {
                "uuid": uuid.uuid4().hex,
                "user_id": self.env.user.id,
                "state": "started",
                "model_name": "lock.code",
                "method_name": "_sync_create",
            }
        )
        code.sudo().queue_job_ids |= job
        return code, job

    def test_never_granted_code_fails_with_its_job(self):
        code, job = self._code_with_job(vendor_grant_ref=False, pin=False)
        job.write({"state": "failed"})
        self.assertTrue(code.failed)
        self.assertEqual(code.state, "failed")

    def test_reconciliation_issues_a_new_credential(self):
        """The point of failing it: a ``pending`` code counts as live and
        blocks the room; a ``failed`` one does not, so a new credential is
        created for the room."""
        code, job = self._code_with_job(vendor_grant_ref=False, pin=False)
        job.write({"state": "failed"})
        with trap_jobs():
            self.reservation._sync_lock_codes()
        new_codes = self.reservation.lock_code_ids - code
        self.assertEqual(len(new_codes), 1)
        self.assertEqual(new_codes.room_id, code.room_id)

    def test_granted_code_is_left_alone(self):
        code, job = self._code_with_job()
        job.write({"state": "failed"})
        self.assertFalse(code.failed)

    def test_cancelled_code_stays_cancelled(self):
        code, job = self._code_with_job(
            vendor_grant_ref=False, pin=False, cancelled=True
        )
        job.write({"state": "failed"})
        self.assertFalse(code.failed)
        self.assertEqual(code.state, "cancelled")

    def test_job_postponed_or_done_does_not_fail_the_code(self):
        code, job = self._code_with_job(vendor_grant_ref=False, pin=False)
        job.write({"state": "pending"})
        job.write({"state": "done"})
        self.assertFalse(code.failed)
