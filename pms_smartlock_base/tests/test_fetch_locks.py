from unittest.mock import MagicMock, patch

from odoo.exceptions import UserError

from .common import CommonSmartlock


class TestFetchLocks(CommonSmartlock):
    """Cover ``lock.vendor.action_fetch_locks``: it asks the connector for the
    locks and renders them as plain text in a transient wizard, staying
    vendor-agnostic and degrading gracefully when a vendor can't list."""

    def setUp(self):
        super().setUp()
        self.connector = MagicMock(name="FakeConnector")
        patcher = patch.object(
            self.env.registry["lock.vendor"],
            "get_connector",
            return_value=self.connector,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_happy_path_renders_name_and_id_per_line(self):
        self.connector.list_locks.return_value = [
            {"id": "31", "name": "101"},
            {"id": "32", "name": "102"},
        ]

        action = self.vendor.action_fetch_locks()

        self.assertEqual(action["res_model"], "lock.list.wizard")
        self.assertEqual(action["target"], "new")
        wizard = self.env["lock.list.wizard"].browse(action["res_id"])
        self.assertEqual(wizard.vendor_id, self.vendor)
        self.assertEqual(wizard.lock_listing, "101\t31\n102\t32")

    def test_empty_listing_shows_placeholder(self):
        self.connector.list_locks.return_value = []

        action = self.vendor.action_fetch_locks()

        wizard = self.env["lock.list.wizard"].browse(action["res_id"])
        self.assertEqual(wizard.lock_listing, "No locks returned.")

    def test_not_implemented_is_caught_as_user_error(self):
        # A vendor whose connector inherits the base default must surface a
        # friendly message, not a raw NotImplementedError traceback.
        self.connector.list_locks.side_effect = NotImplementedError()

        with self.assertRaises(UserError):
            self.vendor.action_fetch_locks()
