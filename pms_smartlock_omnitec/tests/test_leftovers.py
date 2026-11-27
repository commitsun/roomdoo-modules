"""Resumable Omnitec grants: the credential's passcodes are tagged after it,
and what a grant never completed leaves on the locks is deleted by the cleanup
cron."""

import uuid
from datetime import timedelta
from unittest.mock import MagicMock, patch

from roomdoo_locks_base import LockOfflineError

from odoo import fields

from odoo.addons.pms_smartlock_base.tests.common import CommonSmartlock


class TestOmnitecLeftovers(CommonSmartlock):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.vendor.write(
            {
                "vendor_type": "omnitec",
                "omnitec_username": "user",
                "omnitec_password": "pass",
            }
        )

    def setUp(self):
        super().setUp()
        self.reservation = self._create_reservation()
        self.connector = MagicMock(name="FakeOmnitec")
        self.connector.delete_tagged_passcodes.return_value = 1
        patcher = patch.object(
            self.env.registry["lock.vendor"],
            "get_connector",
            return_value=self.connector,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _abandoned_code(self, job_state="failed", retry=1, **code_vals):
        """A credential never granted, with one create job in ``job_state``
        that ran ``retry`` times."""
        vals = {"vendor_grant_ref": False, "pin": False, "failed": True}
        vals.update(code_vals)
        code = self._plant_live_code(self.reservation, **vals)
        QueueJob = self.env["queue.job"].sudo()
        job = QueueJob.with_context(_job_edit_sentinel=QueueJob.EDIT_SENTINEL).create(
            {
                "uuid": uuid.uuid4().hex,
                "user_id": self.env.user.id,
                "state": job_state,
                "retry": retry,
                "model_name": "lock.code",
                "method_name": "_sync_create",
            }
        )
        code.sudo().queue_job_ids |= job
        return code

    def _run_cron(self):
        self.env["lock.code"]._cron_purge_omnitec_leftovers()

    def _cleaned_tags(self):
        return {
            c.args[1] for c in self.connector.delete_tagged_passcodes.call_args_list
        }

    def test_tag_is_booking_reference_and_credential(self):
        code = self._plant_live_code(self.reservation)
        self.assertEqual(
            code._omnitec_passcode_tag(), f"{self.reservation.name}#{code.id}"
        )

    def test_failed_create_is_cleaned_up(self):
        code = self._abandoned_code(job_state="failed")
        self._run_cron()
        self.connector.delete_tagged_passcodes.assert_called_once_with(
            [self.room_a.lock_device_id], code._omnitec_passcode_tag()
        )

    def test_cleanup_covers_the_shared_doors(self):
        common = self._add_common_lock(self.room_a)
        self._abandoned_code(job_state="failed")
        self._run_cron()
        lock_ids = self.connector.delete_tagged_passcodes.call_args.args[0]
        self.assertEqual(lock_ids, [self.room_a.lock_device_id, common.lock_device_id])

    def test_cancelled_between_attempts_is_cleaned_up(self):
        """Cancelled while a retry was pending: the last attempt exited
        without calling the vendor, leaving what the earlier ones did."""
        code = self._abandoned_code(
            job_state="done", retry=2, failed=False, cancelled=True
        )
        self._run_cron()
        self.assertEqual(self._cleaned_tags(), {code._omnitec_passcode_tag()})

    def test_skips_what_never_left_anything(self):
        # Granted: revoked through its ref, not by tag.
        self._abandoned_code(vendor_grant_ref="ref", failed=True)
        # Cancelled before its only attempt reached the vendor.
        self._abandoned_code(job_state="done", retry=1, failed=False, cancelled=True)
        # A sync still in flight owns the locks.
        self._abandoned_code(job_state="pending", retry=2)
        # Window over: a leftover no longer opens anything.
        now = fields.Datetime.now()
        self._abandoned_code(
            date_from=now - timedelta(days=3), date_to=now - timedelta(hours=1)
        )
        self._run_cron()
        self.connector.delete_tagged_passcodes.assert_not_called()

    def test_skips_other_vendors(self):
        # ``test`` is the vendor type CommonSmartlock registers.
        self.vendor.vendor_type = "test"
        self._abandoned_code(job_state="failed")
        self._run_cron()
        self.connector.delete_tagged_passcodes.assert_not_called()

    def test_busy_locks_are_left_for_the_next_run(self):
        self._abandoned_code(job_state="failed")
        with patch.object(
            self.env.registry["lock.code"], "_try_lock_gateways", return_value=False
        ):
            self._run_cron()
        self.connector.delete_tagged_passcodes.assert_not_called()

    def test_vendor_error_does_not_stop_the_others(self):
        first = self._abandoned_code(job_state="failed")
        second = self._abandoned_code(job_state="failed")
        self.connector.delete_tagged_passcodes.side_effect = [
            LockOfflineError("gateway"),
            1,
        ]
        self._run_cron()
        self.assertEqual(
            self._cleaned_tags(),
            {first._omnitec_passcode_tag(), second._omnitec_passcode_tag()},
        )
