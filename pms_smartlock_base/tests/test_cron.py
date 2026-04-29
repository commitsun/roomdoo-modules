from datetime import timedelta
from unittest.mock import patch

from odoo import fields

from .common import CommonSmartlock


class TestCronGenerateLockCodes(CommonSmartlock):
    """``_cron_generate_lock_codes`` is the safety net: it picks up
    reservations crossing the 24h horizon that nobody else has
    triggered (e.g. nothing was written on them since they were
    booked). Tests verify the search domain — what the cron picks up
    and what it skips. The actual sync work is mocked; we only assert
    that the cron's filter classifies each test reservation
    correctly.

    The DB has many real reservations; we check membership of our
    test ids in the called set rather than the global call count.
    """

    def setUp(self):
        super().setUp()
        self._synced_ids = set()
        patcher = patch.object(
            self.env.registry["pms.reservation"],
            "_sync_lock_codes",
            autospec=True,
            side_effect=lambda r: self._synced_ids.add(r.id),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run_cron(self):
        self.env["pms.reservation"]._cron_generate_lock_codes()

    def test_picks_up_reservation_today(self):
        """The committed branch (live code present) bypasses the
        horizon check, so this test is robust regardless of the
        clock; it asserts only that the cron's search domain
        accepts a today-checkin in a smartlock room."""
        today = fields.Date.context_today(self.env.user)
        reservation = self._create_reservation(
            checkin=today, checkout=today + timedelta(days=2)
        )
        self._plant_live_code(reservation)
        self._run_cron()
        self.assertIn(reservation.id, self._synced_ids)

    def test_picks_up_reservation_tomorrow(self):
        """``checkin <= today + 1d`` is the search ceiling; tomorrow
        must be in. Same horizon-bypass trick as above so the
        predicate doesn't gate the assertion."""
        today = fields.Date.context_today(self.env.user)
        reservation = self._create_reservation(
            checkin=today + timedelta(days=1),
            checkout=today + timedelta(days=3),
        )
        self._plant_live_code(reservation)
        self._run_cron()
        self.assertIn(reservation.id, self._synced_ids)

    def test_skips_reservation_two_days_out(self):
        """Outside the search's [today, today+1d] window — even with
        a live code (which would make the predicate True), the search
        excludes it. The next day's cron run will pick it up."""
        today = fields.Date.context_today(self.env.user)
        reservation = self._create_reservation(
            checkin=today + timedelta(days=2),
            checkout=today + timedelta(days=4),
        )
        self._plant_live_code(reservation)
        self._run_cron()
        self.assertNotIn(reservation.id, self._synced_ids)

    def test_skips_cancelled_reservation_in_window(self):
        today = fields.Date.context_today(self.env.user)
        reservation = self._create_reservation(
            checkin=today, checkout=today + timedelta(days=2)
        )
        self._plant_live_code(reservation)
        reservation.action_cancel()
        self._synced_ids.discard(reservation.id)  # discard the cancel-trigger
        self._run_cron()
        self.assertNotIn(reservation.id, self._synced_ids)

    def test_skips_reservation_without_smartlock_room(self):
        """Search domain filters by
        ``reservation_line_ids.room_id.lock_vendor_id != False``;
        a reservation in a vendor-less room never enters the loop."""
        today = fields.Date.context_today(self.env.user)
        reservation = self._create_reservation(
            checkin=today,
            checkout=today + timedelta(days=2),
            preferred_room_id=self.room_no_lock.id,
        )
        self._run_cron()
        self.assertNotIn(reservation.id, self._synced_ids)

    def test_search_picks_up_out_but_predicate_skips(self):
        """``reservation_type='out'`` is captured by the search (it's
        in a smartlock room, in window, not cancelled) but the
        predicate ``_should_have_lock_codes`` returns False. We
        verify the cron does not invoke ``_sync_lock_codes`` for it.
        We plant a live code to prove the skip is the predicate's
        ``out`` branch and not the horizon branch."""
        today = fields.Date.context_today(self.env.user)
        reservation = self._create_reservation(
            checkin=today,
            checkout=today + timedelta(days=2),
            reservation_type="out",
            closure_reason_id=self.closure_reason.id,
        )
        self._plant_live_code(reservation)
        self._run_cron()
        self.assertNotIn(reservation.id, self._synced_ids)
