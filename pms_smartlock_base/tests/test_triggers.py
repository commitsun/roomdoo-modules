from datetime import timedelta
from unittest.mock import patch

from odoo import fields

from odoo.addons.pms_smartlock_base.models.pms_reservation import (
    _PRECOMMIT_PENDING_KEY,
)

from .common import CommonSmartlock


class TestReservationTriggers(CommonSmartlock):
    """Verify that the right field changes enqueue a smartlock sync, and
    that unrelated fields do not. The bug being guarded against: in
    early development ``preferred_room_id`` was missing from
    ``_TRIGGER_FIELDS``, so room changes recomputed
    ``line.room_id`` via ``_write`` (bypassing the line's ``write``
    override) and no sync was ever enqueued."""

    def _pending_ids(self):
        return self.env.cr.precommit.data.get(_PRECOMMIT_PENDING_KEY, set())

    def _assert_enqueued(self, reservation, msg=None):
        self.assertIn(
            reservation.id,
            self._pending_ids(),
            msg or f"reservation {reservation.id} not enqueued for sync",
        )

    def _assert_not_enqueued(self, reservation, msg=None):
        self.assertNotIn(
            reservation.id,
            self._pending_ids(),
            msg or f"reservation {reservation.id} unexpectedly enqueued",
        )

    def setUp(self):
        super().setUp()
        # Clear precommit data carried over from setUpClass writes.
        self.env.cr.precommit.data.pop(_PRECOMMIT_PENDING_KEY, None)
        self.reservation = self._create_reservation()
        self.env.cr.precommit.data.pop(_PRECOMMIT_PENDING_KEY, None)

    def test_preferred_room_id_change_enqueues_sync(self):
        """Regression: room change via ``preferred_room_id`` must enqueue
        a sync. The line's ``room_id`` is a stored compute with
        ``readonly=False``; framework recomputes bypass the line's
        ``write`` override, so the trigger has to live on the
        reservation."""
        self.reservation.write({"preferred_room_id": self.room_b.id})
        self._assert_enqueued(self.reservation)

    def test_room_type_id_change_enqueues_sync(self):
        """Same rationale as ``preferred_room_id``: ``_compute_room_id``
        also depends on ``room_type_id``."""
        new_type = self.env["pms.room.type"].create(
            {
                "pms_property_ids": [self.pms_property.id],
                "name": "Other Type",
                "default_code": "OT_%s" % self.env.cr.now().strftime("%H%M%S%f"),
                "class_id": self.room_type_class.id,
                "list_price": 60,
            }
        )
        self.reservation.write({"room_type_id": new_type.id})
        self._assert_enqueued(self.reservation)

    def test_checkin_change_enqueues_sync(self):
        self.reservation.write(
            {"checkin": self.reservation.checkin + timedelta(days=1)}
        )
        self._assert_enqueued(self.reservation)

    def test_checkout_change_enqueues_sync(self):
        self.reservation.write(
            {"checkout": self.reservation.checkout + timedelta(days=1)}
        )
        self._assert_enqueued(self.reservation)

    def test_arrival_hour_change_enqueues_sync(self):
        self.reservation.write({"arrival_hour": "16:00"})
        self._assert_enqueued(self.reservation)

    def test_departure_hour_change_enqueues_sync(self):
        self.reservation.write({"departure_hour": "11:00"})
        self._assert_enqueued(self.reservation)

    def test_reservation_type_change_enqueues_sync(self):
        """Switching to/from ``out`` flips whether codes should exist;
        the listener must fire so the predicate gets re-evaluated."""
        # ``closure_reason_id`` is related to ``folio_id.closure_reason_id``;
        # write it on the folio first so the ``_check_closure_reason_id``
        # constraint sees the value when ``reservation_type`` flips
        # to ``out`` (the inverse-then-validate ordering is brittle in CI).
        self.reservation.folio_id.closure_reason_id = self.closure_reason.id
        self.reservation.reservation_type = "out"
        self._assert_enqueued(self.reservation)

    def test_unrelated_field_does_not_enqueue(self):
        """Guard against widening ``_TRIGGER_FIELDS`` accidentally —
        fields with no impact on lock-code windows must not enqueue."""
        self.reservation.write({"adults": 2})
        self._assert_not_enqueued(self.reservation)

    def test_line_room_id_write_enqueues_sync(self):
        """Direct line writes (calendar swap, ``update_reservation_line``
        from the API) hit the line's ``write`` listener — this path
        does not depend on the reservation-level fix."""
        line = self.reservation.reservation_line_ids[0]
        line.with_context(avoid_availability_check=True).write(
            {"room_id": self.room_b.id}
        )
        self._assert_enqueued(self.reservation)

    def test_line_unrelated_field_does_not_enqueue(self):
        line = self.reservation.reservation_line_ids[0]
        line.write({"discount": 10.0})
        self._assert_not_enqueued(self.reservation)

    def test_multiple_writes_coalesce_to_single_sync(self):
        """Multiple trigger writes in the same transaction must result
        in exactly one ``_sync_lock_codes`` call when the precommit
        callback fires. Without the precommit dedup, a vendor path
        that falls back to delete+create would regenerate the PIN
        once per write."""
        # Plant a live code so the predicate is True at flush time.
        self._plant_live_code(self.reservation)
        with patch.object(
            type(self.reservation),
            "_sync_lock_codes",
            autospec=True,
        ) as sync_mock:
            self.reservation.write({"preferred_room_id": self.room_b.id})
            self.reservation.write(
                {"checkin": self.reservation.checkin + timedelta(days=1)}
            )
            self.reservation.write({"arrival_hour": "16:00"})
            self.env.cr.flush()
            self.assertEqual(sync_mock.call_count, 1)

    def test_create_reservation_enqueues_sync(self):
        """``create`` must enqueue too (cron picks up far-future
        reservations from the DB; immediate-horizon ones rely on the
        create listener)."""
        # setUp already cleared pending; create a non-overlapping
        # reservation (different dates) so it doesn't fight with
        # ``self.reservation`` for room availability.
        today = fields.Date.context_today(self.env.user)
        new_res = self._create_reservation(
            checkin=today + timedelta(days=60),
            checkout=today + timedelta(days=63),
        )
        self.assertIn(new_res.id, self._pending_ids())

    def test_cancel_state_invokes_cancel_lock_codes(self):
        """Setting ``state='cancel'`` (via ``action_cancel``) must invoke
        ``_cancel_lock_codes`` so live codes get invalidated even if
        the reservation is far in the future."""
        with patch.object(
            type(self.reservation),
            "_cancel_lock_codes",
            autospec=True,
        ) as cancel_mock:
            self.reservation.action_cancel()
            cancel_mock.assert_called_once()

    def test_done_state_invokes_cancel_lock_codes(self):
        """Same as the cancel listener but for ``done`` — the only
        reliable signal of real checkout (early checkout doesn't trim
        ``reservation.line``, so neither line nor date listeners
        notice). The ``done`` write must fire ``_cancel_lock_codes``
        to revoke access immediately."""
        with patch.object(
            type(self.reservation),
            "_cancel_lock_codes",
            autospec=True,
        ) as cancel_mock:
            self.reservation.state = "done"  # bypass the workflow constraints
            cancel_mock.assert_called_once()

    def test_two_reservations_each_enqueued_independently(self):
        """Precommit dedups *within* a reservation, not across — two
        different reservations must both end up in the pending set."""
        other = self._create_reservation(preferred_room_id=self.room_b.id)
        self.reservation.write(
            {"checkin": self.reservation.checkin + timedelta(days=1)}
        )
        other.write({"checkin": other.checkin + timedelta(days=1)})
        pending = self._pending_ids()
        self.assertIn(self.reservation.id, pending)
        self.assertIn(other.id, pending)
