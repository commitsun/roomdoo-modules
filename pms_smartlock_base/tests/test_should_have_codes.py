from datetime import timedelta
from unittest.mock import patch

from odoo import fields

from .common import CommonSmartlock


class TestShouldHaveLockCodes(CommonSmartlock):
    """The ``_should_have_lock_codes`` predicate gates whether a sync
    runs. Two independent True branches:

    1. **Committed**: there's at least one live ``lock.code``. Once the
       system has issued a code we keep reconciling it on every
       trigger, no matter how far away the reservation is.
    2. **Imminent**: within the 24h horizon, even with no codes yet
       (the cron and create paths drive this).

    False branches: draft/cancel state, ``reservation_type='out'``,
    far-future without codes."""

    def test_draft_returns_false(self):
        reservation = self._create_reservation()
        reservation.state = "draft"  # bypass the workflow constraints
        self.assertFalse(reservation._should_have_lock_codes())

    def test_cancel_returns_false(self):
        reservation = self._create_reservation()
        reservation.action_cancel()
        self.assertFalse(reservation._should_have_lock_codes())

    def test_done_returns_false(self):
        """``done`` is the only reliable signal of real checkout (early
        checkout doesn't trim ``reservation.line``); the predicate must
        skip so a write landing after ``done`` doesn't regenerate
        codes for a guest who already left."""
        reservation = self._create_reservation()
        self._plant_live_code(reservation)
        reservation.state = "done"  # bypass the workflow constraints
        self.assertFalse(reservation._should_have_lock_codes())

    def test_out_reservation_type_returns_false(self):
        """``reservation_type='out'`` is a room blocker without a guest;
        no code should be generated even when within horizon and even
        if a stray code exists (operationally those should be cleaned
        up)."""
        reservation = self._create_reservation(
            reservation_type="out",
            closure_reason_id=self.closure_reason.id,
        )
        self.assertFalse(reservation._should_have_lock_codes())

    def test_out_with_existing_code_still_false(self):
        """Edge: even if a live code exists, switching to ``out``
        should evaluate False so the next sync cancels it."""
        reservation = self._create_reservation()
        self._plant_live_code(reservation)
        # ``closure_reason_id`` is related to ``folio_id.closure_reason_id``;
        # writing it on the reservation in the same call as
        # ``reservation_type=out`` races the inverse with the
        # ``_check_closure_reason_id`` constraint in CI. Writing on
        # the folio first sidesteps the race.
        reservation.folio_id.closure_reason_id = self.closure_reason.id
        reservation.reservation_type = "out"
        self.assertFalse(reservation._should_have_lock_codes())

    def test_far_future_no_codes_returns_false(self):
        """Far-future + no codes → False. The cron will pick it up
        when it crosses the 24h horizon."""
        reservation = self._create_reservation()
        self.assertFalse(reservation._should_have_lock_codes())

    def test_far_future_with_live_code_returns_true(self):
        """Once committed (live code exists), keep reconciling. This is
        the branch that lets a room change far in the future still
        propagate to the vendor."""
        reservation = self._create_reservation()
        self._plant_live_code(reservation)
        self.assertTrue(reservation._should_have_lock_codes())

    def test_within_horizon_no_codes_returns_true(self):
        """Within 24h with no codes yet — the create flow's
        ``action_generate_lock_codes`` path, plus the cron's gate.

        Pin ``fields.Datetime.now`` to one hour before
        ``checkin_datetime`` so the predicate's window
        (``now < checkin_datetime <= now + 24h``) is always exercised
        regardless of the wall clock at test time. Without this, a run
        after the property's default ``arrival_hour`` falls past the
        lower bound and the assertion flips."""
        today = fields.Date.context_today(self.env.user)
        reservation = self._create_reservation(
            checkin=today + timedelta(days=1),
            checkout=today + timedelta(days=3),
        )
        frozen_now = reservation.checkin_datetime - timedelta(hours=1)
        with patch.object(fields.Datetime, "now", return_value=frozen_now):
            self.assertTrue(reservation._should_have_lock_codes())

    def test_pending_code_counts_as_live(self):
        """Pending codes (no ``vendor_code_id`` yet) still keep the
        predicate True so the in-flight sync isn't dropped if a write
        races the vendor response."""
        reservation = self._create_reservation()
        self._plant_live_code(reservation, vendor_code_id=False, pin=False)
        self.assertTrue(reservation._should_have_lock_codes())

    def test_cancelled_code_does_not_count_as_live(self):
        """A cancelled code is no longer protective — it shouldn't
        keep the predicate True on its own."""
        reservation = self._create_reservation()
        self._plant_live_code(reservation, cancelled=True)
        self.assertFalse(reservation._should_have_lock_codes())

    def test_failed_code_does_not_count_as_live(self):
        """Same for failed codes — the sync didn't succeed, the
        predicate's two branches must both be False."""
        reservation = self._create_reservation()
        self._plant_live_code(reservation, failed=True)
        self.assertFalse(reservation._should_have_lock_codes())
