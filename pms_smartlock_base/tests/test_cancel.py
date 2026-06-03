from unittest.mock import patch

from .common import CommonSmartlock


class TestCancelLockCodes(CommonSmartlock):
    """``_cancel_lock_codes`` is the reservation-cancel flow: it must
    invalidate every live code under the reservation. Two paths
    depending on whether the code reached the vendor:

    - ``vendor_grant_ref`` set → enqueue ``_sync_remove`` (vendor
      confirms, then ``cancelled=True`` is written on success).
    - ``vendor_grant_ref`` empty → set ``cancelled=True`` locally; the
      ``_sync_create`` job's own guard skips already-cancelled codes
      so no vendor call ever fires for the orphaned record."""

    def setUp(self):
        super().setUp()
        self._enqueue_calls = []
        patcher = patch.object(
            self.env.registry["lock.code"],
            "_enqueue_sync",
            autospec=True,
            side_effect=self._capture_enqueue,
        )
        self.enqueue_mock = patcher.start()
        self.addCleanup(patcher.stop)

    def _capture_enqueue(self, code, method_name, **kwargs):
        self._enqueue_calls.append((code.id, method_name, kwargs))

    def _enqueue_for(self, code):
        return [c for c in self._enqueue_calls if c[0] == code.id]

    def test_vendor_synced_code_enqueues_remove(self):
        reservation = self._create_reservation()
        code = self._plant_live_code(reservation)
        reservation._cancel_lock_codes()
        self.assertEqual(self._enqueue_for(code), [(code.id, "_sync_remove", {})])
        # safety-first: cancelled flag stays False until vendor confirms
        self.assertFalse(code.cancelled)

    def test_pending_code_cancelled_locally(self):
        reservation = self._create_reservation()
        pending = self._plant_live_code(reservation, vendor_grant_ref=False, pin=False)
        reservation._cancel_lock_codes()
        self.assertFalse(self._enqueue_for(pending))
        self.assertTrue(pending.cancelled)

    def test_already_cancelled_skipped(self):
        """No double-processing: a code already invalidated must not
        be enqueued again. Otherwise a re-cancel would issue a
        ``_sync_remove`` for a code the vendor already deleted."""
        reservation = self._create_reservation()
        code = self._plant_live_code(reservation, cancelled=True)
        reservation._cancel_lock_codes()
        self.assertFalse(self._enqueue_for(code))

    def test_failed_code_skipped(self):
        """Failed codes are out of the lifecycle: don't keep retrying
        the cancel for them — that's the operator's call."""
        reservation = self._create_reservation()
        code = self._plant_live_code(reservation, failed=True)
        reservation._cancel_lock_codes()
        self.assertFalse(self._enqueue_for(code))

    def test_mixed_codes_each_take_their_branch(self):
        """One vendor-synced + one pending + one cancelled on the same
        reservation → each follows its own branch independently."""
        reservation = self._create_reservation()
        synced = self._plant_live_code(reservation)
        pending = self._plant_live_code(
            reservation,
            room=self.room_b,
            vendor_grant_ref=False,
            pin=False,
        )
        already_cancelled = self._plant_live_code(
            reservation,
            room=self.room_no_lock,
            vendor_id=self.vendor.id,
            cancelled=True,
        )

        reservation._cancel_lock_codes()

        self.assertEqual(self._enqueue_for(synced), [(synced.id, "_sync_remove", {})])
        self.assertFalse(self._enqueue_for(pending))
        self.assertTrue(pending.cancelled)
        self.assertFalse(self._enqueue_for(already_cancelled))

    def test_action_cancel_invokes_remove_for_synced_codes(self):
        """End-to-end through the workflow: ``action_cancel`` →
        write({state: cancel}) → our listener → ``_cancel_lock_codes``
        → ``_sync_remove`` enqueued for each synced code."""
        reservation = self._create_reservation()
        code = self._plant_live_code(reservation)
        reservation.action_cancel()
        self.assertEqual(self._enqueue_for(code), [(code.id, "_sync_remove", {})])

    def test_done_state_invokes_remove_for_synced_codes(self):
        """``done`` is the only reliable signal of real checkout — early
        checkout never trims the lines, so neither the line nor the
        date listeners would notice. Switching to ``done`` must
        revoke access immediately, same path as cancel."""
        reservation = self._create_reservation()
        code = self._plant_live_code(reservation)
        reservation.state = "done"  # bypass the workflow constraints
        self.assertEqual(self._enqueue_for(code), [(code.id, "_sync_remove", {})])
        self.assertFalse(code.cancelled)
