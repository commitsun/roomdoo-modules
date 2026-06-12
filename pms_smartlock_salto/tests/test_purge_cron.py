from datetime import timedelta
from unittest.mock import MagicMock, patch

from odoo import fields

from odoo.addons.pms_smartlock_base.tests.common import CommonSmartlock


class TestSaltoPurgeCron(CommonSmartlock):
    """``_cron_purge_salto_grants`` hard-deletes the Salto resources behind
    grants that were revoked (suspended) more than the retention window ago.

    The connector is mocked; the tests assert the cron's *selection* — which
    credentials it purges and which it leaves — and that a vendor failure does
    not strand the rest.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.salto_vendor = cls.env["lock.vendor"].create(
            {
                "name": "Salto Test",
                "vendor_type": "salto",
                "pms_property_id": cls.pms_property.id,
                "salto_username": "user",
                "salto_password": "pass",
                "salto_site_id": "site-1",
            }
        )

    def _make_code(self, ref, *, cancelled=True, purged=False, days_ago=20):
        now = fields.Datetime.now()
        return (
            self.env["lock.code"]
            .sudo()
            .create(
                {
                    "room_id": self.room_a.id,
                    "vendor_id": self.salto_vendor.id,
                    "date_from": now - timedelta(days=days_ago + 2),
                    "date_to": now - timedelta(days=days_ago),
                    "cancelled": cancelled,
                    "purged": purged,
                    "vendor_grant_ref": ref,
                    "pin": "1234",
                }
            )
        )

    def _run_cron(self):
        connector = MagicMock()
        with patch.object(
            self.env.registry["lock.vendor"],
            "get_connector",
            return_value=connector,
        ):
            self.env["lock.code"]._cron_purge_salto_grants()
        return connector

    def test_purges_revoked_grant_past_retention(self):
        code = self._make_code("ref-old", days_ago=20)
        connector = self._run_cron()
        connector.delete_grant.assert_any_call("ref-old")
        self.assertTrue(code.purged)

    def test_skips_grant_within_retention(self):
        code = self._make_code("ref-recent", days_ago=5)
        connector = self._run_cron()
        self.assertNotIn(
            "ref-recent",
            [c.args[0] for c in connector.delete_grant.call_args_list],
        )
        self.assertFalse(code.purged)

    def test_skips_not_revoked_grant(self):
        code = self._make_code("ref-live", cancelled=False, days_ago=20)
        connector = self._run_cron()
        self.assertNotIn(
            "ref-live",
            [c.args[0] for c in connector.delete_grant.call_args_list],
        )
        self.assertFalse(code.purged)

    def test_skips_already_purged_grant(self):
        self._make_code("ref-done", purged=True, days_ago=20)
        connector = self._run_cron()
        self.assertNotIn(
            "ref-done",
            [c.args[0] for c in connector.delete_grant.call_args_list],
        )

    def test_skips_grant_without_vendor_ref(self):
        code = self._make_code(False, days_ago=20)
        connector = self._run_cron()
        connector.delete_grant.assert_not_called()
        self.assertFalse(code.purged)

    def test_vendor_failure_leaves_code_unpurged_and_does_not_strand_others(self):
        from roomdoo_locks_base import LockOperationError

        bad = self._make_code("ref-bad", days_ago=20)
        good = self._make_code("ref-good", days_ago=20)

        def delete_grant(ref):
            if ref == "ref-bad":
                raise LockOperationError("boom")
            return True

        connector = MagicMock()
        connector.delete_grant.side_effect = delete_grant
        with patch.object(
            self.env.registry["lock.vendor"],
            "get_connector",
            return_value=connector,
        ):
            self.env["lock.code"]._cron_purge_salto_grants()
        self.assertFalse(bad.purged)
        self.assertTrue(good.purged)

    def test_connector_build_failure_skips_vendor(self):
        """If building the connector raises (e.g. auth/network down), the cron
        logs and skips that vendor, leaving its codes un-purged for next run."""
        code = self._make_code("ref-x", days_ago=20)
        with patch.object(
            self.env.registry["lock.vendor"],
            "get_connector",
            side_effect=Exception("no connector"),
        ):
            self.env["lock.code"]._cron_purge_salto_grants()
        self.assertFalse(code.purged)
