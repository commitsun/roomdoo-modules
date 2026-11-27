"""``_cron_revoke_expired_codes`` revokes the credentials whose window ended and
that no flow revoked: a reservation that is never checked in stays in
``arrival_delayed`` and never reaches the checkout that would revoke them."""

import uuid
from datetime import timedelta
from unittest.mock import patch

from odoo import fields

from .common import CommonSmartlock


class TestRevokeExpiredCodes(CommonSmartlock):
    def setUp(self):
        super().setUp()
        self.reservation = self._create_reservation()
        self._enqueued = []
        patcher = patch.object(
            self.env.registry["lock.code"],
            "_enqueue_sync",
            autospec=True,
            side_effect=lambda code, method_name, **kwargs: self._enqueued.append(
                (code.id, method_name)
            ),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _expired_code(self, **overrides):
        now = fields.Datetime.now()
        vals = {
            "date_from": now - timedelta(days=3),
            "date_to": now - timedelta(hours=1),
        }
        vals.update(overrides)
        return self._plant_live_code(self.reservation, **vals)

    def _add_job(self, code, method_name, state):
        QueueJob = self.env["queue.job"].sudo()
        job = QueueJob.with_context(_job_edit_sentinel=QueueJob.EDIT_SENTINEL).create(
            {
                "uuid": uuid.uuid4().hex,
                "user_id": self.env.user.id,
                "state": state,
                "model_name": "lock.code",
                "method_name": method_name,
            }
        )
        code.sudo().queue_job_ids |= job

    def _revoked(self, code):
        self.env["lock.code"]._cron_revoke_expired_codes()
        return (code.id, "_sync_remove") in self._enqueued

    def test_expired_code_is_revoked(self):
        self.assertTrue(self._revoked(self._expired_code()))

    def test_expired_failed_code_is_revoked(self):
        """A failed modify leaves the grant on the locks."""
        code = self._expired_code(failed=True)
        self._add_job(code, "_sync_modify", "failed")
        self.assertTrue(self._revoked(code))

    def test_code_still_open_is_left(self):
        code = self._expired_code(date_to=fields.Datetime.now() + timedelta(hours=1))
        self.assertFalse(self._revoked(code))

    def test_code_never_granted_is_left(self):
        code = self._expired_code(vendor_grant_ref=False, pin=False)
        self.assertFalse(self._revoked(code))

    def test_cancelled_code_is_left(self):
        self.assertFalse(self._revoked(self._expired_code(cancelled=True)))

    def test_code_whose_revoke_was_enqueued_is_left(self):
        """A revoke that keeps failing is not retried on every run."""
        code = self._expired_code()
        self._add_job(code, "_sync_remove", "failed")
        self.assertFalse(self._revoked(code))

    def test_code_with_a_sync_in_flight_is_left(self):
        code = self._expired_code()
        self._add_job(code, "_sync_modify", "pending")
        self.assertFalse(self._revoked(code))
