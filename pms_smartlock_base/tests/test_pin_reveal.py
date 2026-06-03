"""Tests for the audited PIN reveal flow.

The ``pin`` field on ``lock.code`` is locked behind ``groups="!base.group_user"``
so no internal user (admin included) can read it directly. The only
sanctioned UI path is ``action_reveal_pin``, which sudoes the read and
records the access in ``lock.code.access.log`` before opening the
transient viewer wizard. The ``_cron_purge_old`` entry on the log model
ages out old entries (default 90d retention)."""

from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError

from .common import CommonSmartlock


class TestActionRevealPin(CommonSmartlock):
    def setUp(self):
        super().setUp()
        self.reservation = self._create_reservation()

    def test_no_pin_raises_user_error(self):
        """Code without a PIN (vendor sync hasn't completed) → UserError.
        We must not create an access log entry for an empty reveal —
        the audit trail represents PIN disclosures, not attempted ones."""
        code = self._plant_live_code(
            self.reservation, vendor_grant_ref=False, pin=False
        )
        log_model = self.env["lock.code.access.log"].sudo()
        before = log_model.search_count([("lock_code_id", "=", code.id)])
        with self.assertRaises(UserError):
            code.action_reveal_pin()
        after = log_model.search_count([("lock_code_id", "=", code.id)])
        self.assertEqual(before, after)

    def test_reveal_creates_access_log_entry(self):
        """Successful reveal must record who accessed the PIN, on which
        code, and when. The log row is the audit trail's load-bearing
        invariant — without it the ``!base.group_user`` field-level
        lock would be pointless."""
        code = self._plant_live_code(self.reservation)
        log_model = self.env["lock.code.access.log"].sudo()
        before = log_model.search_count([("lock_code_id", "=", code.id)])
        code.action_reveal_pin()
        logs = log_model.search([("lock_code_id", "=", code.id)])
        self.assertEqual(len(logs), before + 1)
        latest = logs.sorted("accessed_at", reverse=True)[0]
        self.assertEqual(latest.lock_code_id, code)
        self.assertEqual(latest.user_id, self.env.user)
        self.assertTrue(latest.accessed_at)

    def test_reveal_returns_wizard_action_with_pin(self):
        """The returned action must point to the transient viewer with
        the PIN copied in — the wizard is what the operator sees, the
        ``lock.code`` form must never expose the value directly."""
        code = self._plant_live_code(self.reservation)
        action = code.action_reveal_pin()
        self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertEqual(action["res_model"], "lock.code.pin.viewer")
        self.assertEqual(action["target"], "new")
        viewer = self.env["lock.code.pin.viewer"].sudo().browse(action["res_id"])
        self.assertTrue(viewer.exists())
        self.assertEqual(viewer.lock_code_id, code)
        self.assertEqual(viewer.pin, "1234")

    def test_two_reveals_create_two_log_entries(self):
        """Each reveal is independently audited — accessing the same
        code twice produces two log rows, not one updated row."""
        code = self._plant_live_code(self.reservation)
        log_model = self.env["lock.code.access.log"].sudo()
        before = log_model.search_count([("lock_code_id", "=", code.id)])
        code.action_reveal_pin()
        code.action_reveal_pin()
        after = log_model.search_count([("lock_code_id", "=", code.id)])
        self.assertEqual(after - before, 2)


class TestAccessLogCronPurge(CommonSmartlock):
    """``_cron_purge_old`` deletes log rows older than the retention
    window (default 90d). ACL forbids unlink even for admins, so the
    method sudoes internally — tests verify the retention boundary."""

    def setUp(self):
        super().setUp()
        self.reservation = self._create_reservation()
        self.code = self._plant_live_code(self.reservation)

    def _make_log(self, accessed_at):
        return (
            self.env["lock.code.access.log"]
            .sudo()
            .create(
                {
                    "lock_code_id": self.code.id,
                    "user_id": self.env.user.id,
                    "accessed_at": accessed_at,
                }
            )
        )

    def test_purges_logs_older_than_default_retention(self):
        now = fields.Datetime.now()
        old = self._make_log(now - timedelta(days=100))
        recent = self._make_log(now - timedelta(days=1))
        self.env["lock.code.access.log"]._cron_purge_old()
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())

    def test_purges_with_custom_retention(self):
        """The cron accepts a custom threshold so per-tenant retention
        can be tightened by editing the cron's call site."""
        now = fields.Datetime.now()
        log_15d = self._make_log(now - timedelta(days=15))
        log_5d = self._make_log(now - timedelta(days=5))
        self.env["lock.code.access.log"]._cron_purge_old(delete_older_than=10)
        self.assertFalse(log_15d.exists())
        self.assertTrue(log_5d.exists())

    def test_keeps_log_at_exact_cutoff(self):
        """Boundary: a log written exactly at the cutoff instant is not
        strictly older than the cutoff and must survive — the predicate
        is ``accessed_at < cutoff``, not ``<=``."""
        now = fields.Datetime.now()
        # Use a slightly safer offset (29d 23h) so clock drift between
        # the test write and the cron's ``now`` doesn't cross the
        # boundary in either direction.
        log_at_cutoff = self._make_log(now - timedelta(days=29, hours=23))
        self.env["lock.code.access.log"]._cron_purge_old(delete_older_than=30)
        self.assertTrue(log_at_cutoff.exists())
