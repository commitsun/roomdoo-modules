from unittest.mock import MagicMock, patch

from psycopg2 import IntegrityError

from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import TestBookaiCommon


@tagged("post_install", "-at_install")
class TestBookaiTool(TestBookaiCommon):
    @mute_logger("odoo.sql_db")
    def test_name_type_unique_constraint(self):
        self.env["bookai.tool"].create(
            {
                "name": "dup.tool",
                "description": "First",
                "tool_type": "sdk",
            }
        )
        with self.assertRaises(IntegrityError), self.cr.savepoint():
            self.env["bookai.tool"].create(
                {
                    "name": "dup.tool",
                    "description": "Second",
                    "tool_type": "sdk",
                }
            )

    def test_name_type_unique_different_type_ok(self):
        self.env["bookai.tool"].create(
            {
                "name": "multi.tool",
                "description": "SDK",
                "tool_type": "sdk",
            }
        )
        tool2 = self.env["bookai.tool"].create(
            {
                "name": "multi.tool",
                "description": "MCP",
                "tool_type": "mcp",
            }
        )
        self.assertTrue(tool2.id)

    def test_compute_agent_count(self):
        self.env["bookai.agent.tool.binding"].create(
            {"agent_id": self.agent.id, "tool_id": self.tool_sdk.id}
        )
        self.tool_sdk.invalidate_recordset()
        self.assertEqual(self.tool_sdk.agent_count, 1)

    def test_sync_sdk_tools_creates_new(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "tools": [
                {
                    "name": "synced.tool",
                    "description": "From API",
                    "input_schema": {"type": "object"},
                    "sdk_method": "synced.method",
                }
            ]
        }
        mock_resp.raise_for_status = MagicMock()
        with patch("requests.get", return_value=mock_resp):
            self.env["bookai.tool"].action_sync_sdk_tools()
        tool = self.env["bookai.tool"].search(
            [("name", "=", "synced.tool"), ("tool_type", "=", "sdk")]
        )
        self.assertTrue(tool)
        self.assertEqual(tool.description, "From API")

    def test_sync_sdk_tools_updates_existing(self):
        tool = self.env["bookai.tool"].create(
            {
                "name": "update.tool",
                "description": "Old desc",
                "tool_type": "sdk",
                "sdk_method": "old.method",
            }
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "tools": [
                {
                    "name": "update.tool",
                    "description": "New desc",
                    "input_schema": {},
                    "sdk_method": "new.method",
                }
            ]
        }
        mock_resp.raise_for_status = MagicMock()
        with patch("requests.get", return_value=mock_resp):
            self.env["bookai.tool"].action_sync_sdk_tools()
        tool.invalidate_recordset()
        self.assertEqual(tool.description, "New desc")

    def test_sync_sdk_tools_archives_stale(self):
        tool = self.env["bookai.tool"].create(
            {
                "name": "stale.tool",
                "description": "Will be archived",
                "tool_type": "sdk",
            }
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"tools": []}
        mock_resp.raise_for_status = MagicMock()
        with patch("requests.get", return_value=mock_resp):
            self.env["bookai.tool"].action_sync_sdk_tools()
        tool.invalidate_recordset()
        self.assertFalse(tool.active)

    def test_sync_sdk_tools_skips_without_config(self):
        self.icp.set_param("pms_bookai.api_endpoint", "")
        self.icp.set_param("pms_bookai.api_token", "")
        with patch("requests.get") as mock_get:
            self.env["bookai.tool"].action_sync_sdk_tools()
        mock_get.assert_not_called()
