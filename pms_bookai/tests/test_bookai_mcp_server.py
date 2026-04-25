from unittest.mock import MagicMock, patch

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import TestBookaiCommon

WEBHOOK_PATH = "odoo.addons.pms_bookai.models.bookai_webhook_mixin.requests.post"
MCP_REQUEST_PATH = "odoo.addons.pms_bookai.models.bookai_mcp_server.requests"


@tagged("post_install", "-at_install")
class TestBookaiMcpServer(TestBookaiCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.server_stdio = cls.env["bookai.mcp.server"].create(
            {
                "name": "Test Stdio",
                "transport_type": "stdio",
                "command": "npx",
                "args": "@test/mcp-server",
                "env_vars": '{"API_KEY": "test123"}',
            }
        )
        cls.server_http = cls.env["bookai.mcp.server"].create(
            {
                "name": "Test HTTP",
                "transport_type": "http",
                "url": "https://mcp.test",
                "api_key": "key123",
                "auth_type": "bearer",
            }
        )

    def test_build_server_payload_stdio(self):
        payload = self.server_stdio._build_server_payload()
        self.assertEqual(payload["transport_type"], "stdio")
        self.assertEqual(payload["command"], "npx")
        self.assertEqual(payload["args"], "@test/mcp-server")
        self.assertEqual(payload["env_vars"], {"API_KEY": "test123"})

    def test_build_server_payload_http(self):
        payload = self.server_http._build_server_payload()
        self.assertEqual(payload["transport_type"], "http")
        self.assertEqual(payload["url"], "https://mcp.test")
        self.assertEqual(payload["api_key"], "key123")

    def test_build_server_payload_env_vars_invalid(self):
        self.server_stdio.write({"env_vars": "not-json"})
        payload = self.server_stdio._build_server_payload()
        self.assertEqual(payload["env_vars"], {})

    def test_bookai_request_validates_method(self):
        with self.assertRaises(ValueError):
            self.server_stdio._bookai_request("INVALID", "/test")

    def test_bookai_request_raises_without_config(self):
        self.icp.set_param("pms_bookai.api_endpoint", "")
        self.icp.set_param("pms_bookai.api_token", "")
        with self.assertRaises(UserError):
            self.server_stdio._bookai_request("get", "/test")

    def test_discover_tools_creates_and_archives(self):
        # Pre-existing tool to be archived
        stale = self.env["bookai.tool"].create(
            {
                "name": "stale-mcp-tool",
                "description": "Will archive",
                "tool_type": "mcp",
                "mcp_server_id": self.server_stdio.id,
                "mcp_tool_name": "stale-mcp-tool",
            }
        )
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "tools": [
                {
                    "name": "new-tool",
                    "description": "Discovered",
                    "inputSchema": {"type": "object"},
                }
            ]
        }
        mock_resp.raise_for_status = MagicMock()
        with patch(f"{MCP_REQUEST_PATH}.post", return_value=mock_resp):
            self.server_stdio.action_discover_tools()
        new_tool = self.env["bookai.tool"].search(
            [
                ("mcp_server_id", "=", self.server_stdio.id),
                ("mcp_tool_name", "=", "new-tool"),
            ]
        )
        self.assertTrue(new_tool)
        stale.invalidate_recordset()
        self.assertFalse(stale.active)

    def test_action_connect_updates_status(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "status": "ok",
            "message": "Connected",
        }
        mock_resp.raise_for_status = MagicMock()
        with patch(f"{MCP_REQUEST_PATH}.post", return_value=mock_resp):
            self.server_stdio.action_connect()
        self.assertEqual(self.server_stdio.connection_status, "connected")

    def test_action_disconnect_resets_status(self):
        self.server_stdio.write({"connection_status": "connected"})
        mock_resp = MagicMock()
        mock_resp.json.return_value = {}
        mock_resp.raise_for_status = MagicMock()
        with patch(f"{MCP_REQUEST_PATH}.post", return_value=mock_resp):
            self.server_stdio.action_disconnect()
        self.assertEqual(self.server_stdio.connection_status, "disconnected")

    def test_action_check_status_unreachable(self):
        with patch.object(
            type(self.server_stdio),
            "_bookai_request",
            side_effect=UserError("unreachable"),
        ):
            self.server_stdio.action_check_status()
        self.assertEqual(self.server_stdio.connection_status, "error")

    def test_health_check_all_reconnects_disconnected(self):
        self.server_stdio.write({"connection_status": "disconnected"})
        mock_status_resp = MagicMock()
        mock_status_resp.json.return_value = {"servers": []}
        mock_status_resp.raise_for_status = MagicMock()

        mock_connect_resp = MagicMock()
        mock_connect_resp.json.return_value = {
            "status": "ok",
            "message": "",
        }
        mock_connect_resp.raise_for_status = MagicMock()
        with patch(
            f"{MCP_REQUEST_PATH}.get",
            return_value=mock_status_resp,
        ), patch(
            f"{MCP_REQUEST_PATH}.post",
            return_value=mock_connect_resp,
        ):
            self.env["bookai.mcp.server"].action_health_check_all()
        self.server_stdio.invalidate_recordset()
        self.assertEqual(self.server_stdio.connection_status, "connected")

    def test_write_status_only_skips_webhook(self):
        with patch(WEBHOOK_PATH) as mock_post:
            self.server_stdio.write({"connection_status": "error"})
        mock_post.assert_not_called()

    def test_write_name_triggers_webhook(self):
        with patch(WEBHOOK_PATH) as mock_post:
            self.server_stdio.write({"name": "Renamed Server"})
        self.assertTrue(mock_post.called)
