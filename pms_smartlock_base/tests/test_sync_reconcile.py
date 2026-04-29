from datetime import datetime, time, timedelta
from unittest.mock import patch

from odoo import fields

from .common import CommonSmartlock


class TestSyncReconcile(CommonSmartlock):
    """``_sync_lock_codes`` is the heart of the module: it reconciles
    ``lock.code`` records against the reservation's current room
    windows. Three branches exist (cancel-removed, modify-shifted,
    create-new) plus a vendor-vs-pending split on each. We exercise
    each branch in isolation and assert what was enqueued — never
    actually running the vendor sync."""

    def setUp(self):
        super().setUp()
        # Capture every ``_enqueue_sync`` call as ``(record, method, kwargs)``
        # so tests assert on the exact reconciliation decisions.
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
        return None

    def _enqueue_for(self, code):
        return [c for c in self._enqueue_calls if c[0] == code.id]

    def test_create_branch_new_room_in_target(self):
        """No live code yet → ``_sync_lock_codes`` creates a fresh
        ``lock.code`` and enqueues ``_sync_create``."""
        reservation = self._create_reservation()
        reservation._sync_lock_codes()
        codes = reservation.lock_code_ids
        self.assertEqual(len(codes), 1)
        self.assertEqual(codes.room_id, self.room_a)
        self.assertFalse(codes.vendor_code_id)
        self.assertEqual(self._enqueue_for(codes), [(codes.id, "_sync_create", {})])

    def test_create_skipped_when_room_has_no_lock(self):
        """Rooms without ``lock_vendor_id`` and ``lock_device_id`` are
        excluded from the target windows — no ``lock.code`` is created
        for them."""
        reservation = self._create_reservation(preferred_room_id=self.room_no_lock.id)
        reservation._sync_lock_codes()
        self.assertFalse(reservation.lock_code_ids)
        self.assertFalse(self._enqueue_calls)

    def test_remove_branch_vendor_code_present(self):
        """Live code's room is no longer in the target → ``_sync_remove``
        is enqueued; the code is **not** locally cancelled (waiting on
        the vendor's confirmation, per the safety-first invariant)."""
        reservation = self._create_reservation()
        live = self._plant_live_code(reservation)
        # Force the live code's room to differ from any target window
        live.write({"room_id": self.room_no_lock.id})
        reservation._sync_lock_codes()
        self.assertEqual(self._enqueue_for(live), [(live.id, "_sync_remove", {})])
        self.assertFalse(live.cancelled)

    def test_remove_branch_pending_code_cancelled_locally(self):
        """Pending code (no ``vendor_code_id`` yet) for a room no
        longer in target → cancel locally, no vendor call. The
        original create job hasn't run yet, so there's nothing on
        the lock to invalidate."""
        reservation = self._create_reservation()
        pending = self._plant_live_code(reservation, vendor_code_id=False, pin=False)
        pending.write({"room_id": self.room_no_lock.id})
        reservation._sync_lock_codes()
        self.assertFalse(self._enqueue_for(pending))
        self.assertTrue(pending.cancelled)

    def test_modify_branch_dates_shifted(self):
        """Same room, shifted window → ``_sync_modify`` with new dates."""
        reservation = self._create_reservation()
        live = self._plant_live_code(reservation)
        original_from = live.date_from
        # Shift the reservation forward; line dates and checkin_datetime move.
        reservation.write(
            {
                "checkin": reservation.checkin + timedelta(days=2),
                "checkout": reservation.checkout + timedelta(days=2),
            }
        )
        # Reset captures so we only see the explicit sync below
        self._enqueue_calls.clear()
        reservation._sync_lock_codes()
        calls = self._enqueue_for(live)
        self.assertEqual(len(calls), 1)
        _, method, kwargs = calls[0]
        self.assertEqual(method, "_sync_modify")
        self.assertNotEqual(kwargs["date_from"], original_from)
        self.assertEqual(kwargs["date_from"], reservation.checkin_datetime)
        self.assertEqual(kwargs["date_to"], reservation.checkout_datetime)

    def test_modify_skipped_when_dates_equal(self):
        """Idempotency: same room, same window → no enqueue. Without
        this guard, every unrelated trigger would re-issue a
        modify and burn vendor calls (and on delete-fallback vendors,
        regenerate the PIN)."""
        reservation = self._create_reservation()
        self._plant_live_code(reservation)
        reservation._sync_lock_codes()
        self.assertFalse(self._enqueue_calls)

    def test_modify_skipped_for_pending_code_even_if_dates_shifted(self):
        """Pending codes (no ``vendor_code_id``) keep their original
        dates even when the reservation window shifted — the in-flight
        ``_sync_create`` job will use whatever dates were on the
        record at enqueue time. Updating them now would write
        vendor-bound state before the vendor confirmed."""
        reservation = self._create_reservation()
        pending = self._plant_live_code(reservation, vendor_code_id=False, pin=False)
        # Shift the window — line dates move, but pending code shouldn't modify
        reservation.write(
            {
                "checkin": reservation.checkin + timedelta(days=2),
                "checkout": reservation.checkout + timedelta(days=2),
            }
        )
        self._enqueue_calls.clear()
        reservation._sync_lock_codes()
        self.assertFalse(self._enqueue_for(pending))

    def test_room_swap_cancels_old_creates_new(self):
        """Full reconcile of a room change end-to-end: old code goes
        through ``_sync_remove``, new code is created with
        ``_sync_create``. The two ``lock.code`` records coexist
        until the vendor confirms the cancel — by design."""
        reservation = self._create_reservation()
        old = self._plant_live_code(reservation)
        reservation.write({"preferred_room_id": self.room_b.id})
        self._enqueue_calls.clear()
        reservation._sync_lock_codes()
        # Old code: remove
        self.assertEqual([c[1] for c in self._enqueue_for(old)], ["_sync_remove"])
        # New code: create
        new = reservation.lock_code_ids - old
        self.assertEqual(len(new), 1)
        self.assertEqual(new.room_id, self.room_b)
        self.assertEqual([c[1] for c in self._enqueue_for(new)], ["_sync_create"])

    def test_cancelled_codes_ignored_by_reconcile(self):
        """Cancelled codes shouldn't participate as live: a new sync
        on the same room must create a fresh code rather than try to
        modify the cancelled one."""
        reservation = self._create_reservation()
        self._plant_live_code(reservation, cancelled=True)
        reservation._sync_lock_codes()
        live_after = reservation.lock_code_ids.filtered(lambda c: not c.cancelled)
        self.assertEqual(len(live_after), 1)
        self.assertEqual(
            [c[1] for c in self._enqueue_for(live_after)], ["_sync_create"]
        )


class TestBuildLockCodeWindows(CommonSmartlock):
    """``_build_lock_code_windows`` groups the reservation's lines into
    contiguous (room, from, to) tuples — the input the reconciler
    consumes to know what codes the vendor should hold."""

    def _set_line_room(self, line, room):
        """Write ``room_id`` on a line bypassing availability checks."""
        line.with_context(avoid_availability_check=True).write({"room_id": room.id})

    def test_single_room_single_window(self):
        reservation = self._create_reservation()
        windows = reservation._build_lock_code_windows()
        self.assertEqual(len(windows), 1)
        room, date_from, date_to = windows[0]
        self.assertEqual(room, self.room_a)
        self.assertEqual(date_from, reservation.checkin_datetime)
        self.assertEqual(date_to, reservation.checkout_datetime)

    def test_split_rooms_split_window_at_departure_hour(self):
        """If different lines have different rooms, the outgoing room's
        window ends and the incoming room's begins at the property's
        ``default_departure_hour`` of the transition date — the same
        instant for both, so the guest can transition without a gap."""
        reservation = self._create_reservation(
            checkin=fields.Date.context_today(self.env.user) + timedelta(days=30),
            checkout=fields.Date.context_today(self.env.user) + timedelta(days=33),
        )
        sorted_lines = reservation.reservation_line_ids.sorted("date")
        # First night room_a, next two nights room_b
        self._set_line_room(sorted_lines[1], self.room_b)
        self._set_line_room(sorted_lines[2], self.room_b)

        windows = reservation._build_lock_code_windows()
        self.assertEqual(len(windows), 2)
        first_room, first_from, first_to = windows[0]
        second_room, second_from, second_to = windows[1]
        self.assertEqual(first_room, self.room_a)
        self.assertEqual(second_room, self.room_b)
        self.assertEqual(first_from, reservation.checkin_datetime)
        self.assertEqual(second_to, reservation.checkout_datetime)
        # Transition: same instant, at default_departure_hour of day 2
        self.assertEqual(first_to, second_from)
        hour, minute = (
            int(self.pms_property.default_departure_hour[0:2]),
            int(self.pms_property.default_departure_hour[3:5]),
        )
        transition_local = datetime.combine(sorted_lines[1].date, time(hour, minute))
        expected = self.pms_property.date_property_timezone(transition_local)
        self.assertEqual(first_to, expected)

    def test_room_without_lock_excluded_from_windows(self):
        """Even if the line is assigned, a room without
        ``lock_vendor_id`` or ``lock_device_id`` is filtered out:
        we have nothing to sync to."""
        reservation = self._create_reservation(preferred_room_id=self.room_no_lock.id)
        self.assertFalse(reservation._build_lock_code_windows())

    def test_partial_lock_coverage_yields_partial_windows(self):
        """A reservation that splits between a smartlock room and a
        non-smartlock room produces a window only for the smartlock
        nights — the others are silently dropped (no code = no
        sync, the property simply uses traditional keys for those
        rooms)."""
        reservation = self._create_reservation(
            checkin=fields.Date.context_today(self.env.user) + timedelta(days=30),
            checkout=fields.Date.context_today(self.env.user) + timedelta(days=33),
        )
        sorted_lines = reservation.reservation_line_ids.sorted("date")
        # Switch the last night to the lock-less room
        self._set_line_room(sorted_lines[2], self.room_no_lock)

        windows = reservation._build_lock_code_windows()
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0][0], self.room_a)
