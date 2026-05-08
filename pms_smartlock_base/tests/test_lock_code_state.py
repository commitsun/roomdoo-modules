"""Tests for the ``state`` computed field on ``lock.code``, focusing on
the ``syncing`` branch derived from ``queue_job_ids``. The other states
(``active``/``scheduled``/``pending``/``expired``/``failed``/``cancelled``)
are exercised indirectly by the rest of the suite — here we only cover
the new branch and its priority against the existing ones."""

import uuid
from datetime import timedelta

from odoo import fields

from .common import CommonSmartlock


class _StateTestBase(CommonSmartlock):
    def setUp(self):
        super().setUp()
        self.reservation = self._create_reservation()
        self.code = self._plant_live_code(self.reservation)
        # Force the planted code into the ``active`` validity window so
        # the "no jobs" baseline is ``active`` and the syncing branch
        # has something to override.
        now = fields.Datetime.now()
        self.code.write(
            {
                "date_from": now - timedelta(hours=1),
                "date_to": now + timedelta(hours=1),
            }
        )

    def _make_job(self, state, method_name="_sync_modify"):
        """Create a ``queue.job`` row directly (bypassing ``with_delay``)
        and link it to ``self.code``. The ``_job_edit_sentinel`` context
        is the OCA-blessed escape hatch for seeding job rows in tests."""
        QueueJob = self.env["queue.job"]
        job = QueueJob.with_context(_job_edit_sentinel=QueueJob.EDIT_SENTINEL).create(
            {
                "uuid": uuid.uuid4().hex,
                "user_id": self.env.user.id,
                "state": state,
                "model_name": "lock.code",
                "method_name": method_name,
            }
        )
        self.code.sudo().queue_job_ids |= job
        return job


class TestSyncingState(_StateTestBase):
    def test_pending_job_yields_syncing(self):
        self._make_job("pending")
        self.assertEqual(self.code.state, "syncing")

    def test_enqueued_job_yields_syncing(self):
        self._make_job("enqueued")
        self.assertEqual(self.code.state, "syncing")

    def test_started_job_yields_syncing(self):
        self._make_job("started")
        self.assertEqual(self.code.state, "syncing")

    def test_done_job_falls_through_to_base_state(self):
        """Once the job lands in ``done``, ``syncing`` no longer applies
        and the state reverts to whatever the base fields say (here
        ``active`` because the planted code is in its validity window)."""
        self._make_job("done")
        self.assertEqual(self.code.state, "active")

    def test_failed_job_alone_does_not_yield_syncing(self):
        """A queue.job stuck in ``failed`` is not in flight; the base
        state wins. ``code.failed`` is the field that flips the lifecycle
        to ``failed``, set explicitly by the sync method on permanent
        vendor errors — not by the job's own state."""
        self._make_job("failed")
        self.assertEqual(self.code.state, "active")

    def test_mixed_jobs_one_in_flight_yields_syncing(self):
        """If any linked job is in flight, the code is syncing — older
        ``done``/``failed`` jobs in ``queue_job_ids`` are kept as audit
        trail and must not mask a live retry."""
        self._make_job("done")
        self._make_job("started")
        self.assertEqual(self.code.state, "syncing")


class TestSyncingStatePriority(_StateTestBase):
    def test_cancelled_wins_over_syncing(self):
        self._make_job("started")
        self.code.cancelled = True
        self.assertEqual(self.code.state, "cancelled")

    def test_failed_wins_over_syncing(self):
        """A retry job after a permanent failure shouldn't hide the
        ``failed`` flag; the operator needs to see that the previous
        attempt failed even while a new one is in flight."""
        self._make_job("started")
        self.code.failed = True
        self.assertEqual(self.code.state, "failed")

    def test_syncing_wins_over_active(self):
        """The original UX bug: a code with new dates being pushed to
        the vendor used to keep showing ``active`` (green) until the
        job confirmed. Now it shows ``syncing`` (warning)."""
        # Code is in its validity window → would be "active" without a job.
        self.assertEqual(self.code.state, "active")
        self._make_job("enqueued")
        self.assertEqual(self.code.state, "syncing")

    def test_syncing_wins_over_pending(self):
        """A freshly-created code with no ``vendor_code_id`` and a
        ``_sync_create`` job in flight should read ``syncing`` (we know
        something is happening) rather than ``pending`` (which now
        means "stuck without a job")."""
        self.code.sudo().write({"vendor_code_id": False, "pin": False})
        self.assertEqual(self.code.state, "pending")
        self._make_job("enqueued", method_name="_sync_create")
        self.assertEqual(self.code.state, "syncing")


class TestSyncingStateSearch(_StateTestBase):
    def _plant_active_code(self, room):
        now = fields.Datetime.now()
        return self._plant_live_code(
            self.reservation,
            room=room,
            vendor_code_id="vendor-code-2",
            date_from=now - timedelta(hours=1),
            date_to=now + timedelta(hours=1),
        )

    def test_search_syncing_returns_codes_with_jobs_in_flight(self):
        other_code = self._plant_active_code(self.room_b)
        self._make_job("started")  # links to self.code

        # Restrict to the reservation's codes so the assertion doesn't
        # depend on unrelated rows already in the test DB.
        results = self.reservation.lock_code_ids.search(
            [
                ("id", "in", self.reservation.lock_code_ids.ids),
                ("state", "=", "syncing"),
            ]
        )

        self.assertIn(self.code, results)
        self.assertNotIn(other_code, results)

    def test_search_active_excludes_syncing_codes(self):
        """Mutual exclusivity: a code with an in-flight job is not in
        the ``active`` search result, because its canonical state is
        ``syncing``."""
        other_code = self._plant_active_code(self.room_b)
        self._make_job("started")

        results = self.env["lock.code"].search(
            [("id", "in", self.reservation.lock_code_ids.ids), ("state", "=", "active")]
        )

        self.assertNotIn(self.code, results)
        self.assertIn(other_code, results)
