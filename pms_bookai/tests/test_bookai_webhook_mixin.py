from unittest.mock import patch

import requests

from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import TestBookaiCommon

WEBHOOK_LOGGER = "odoo.addons.pms_bookai.models.bookai_webhook_mixin"


@tagged("post_install", "-at_install")
class TestBookaiWebhookMixin(TestBookaiCommon):
    def test_get_bookai_config_returns_params(self):
        base_url, token = self.agent._get_bookai_config()
        self.assertEqual(base_url, "https://bookai.test")
        self.assertEqual(token, "test-token-123")

    def test_get_bookai_config_empty_when_not_set(self):
        self.icp.set_param("pms_bookai.api_endpoint", "")
        self.icp.set_param("pms_bookai.api_token", "")
        base_url, token = self.agent._get_bookai_config()
        self.assertEqual(base_url, "")
        self.assertEqual(token, "")

    def test_notify_webhook_skipped_without_config(self):
        self.icp.set_param("pms_bookai.api_endpoint", "")
        self.icp.set_param("pms_bookai.api_token", "")
        with patch("requests.post") as mock_post:
            self.agent._notify_bookai_webhook("upsert")
        mock_post.assert_not_called()

    def test_notify_webhook_posts_payload(self):
        with patch(
            "odoo.addons.pms_bookai.models.bookai_webhook_mixin." "requests.post"
        ) as mock_post:
            self.agent._notify_bookai_webhook("upsert")
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        self.assertIn("/webhooks/agent-updated", call_kwargs[0][0])

    def test_notify_webhook_delete_sends_action_delete(self):
        data = [{"agent_id": 99, "technical_name": "deleted"}]
        with patch(
            "odoo.addons.pms_bookai.models.bookai_webhook_mixin." "requests.post"
        ) as mock_post:
            self.agent._notify_bookai_webhook_delete(data)
        mock_post.assert_called_once()
        import json

        payload = json.loads(mock_post.call_args[1]["data"])
        self.assertEqual(payload["action"], "delete")
        self.assertEqual(payload["type"], "agent_updated")

    @mute_logger(WEBHOOK_LOGGER)
    def test_post_webhook_catches_connection_error(self):
        with patch(
            "odoo.addons.pms_bookai.models.bookai_webhook_mixin." "requests.post",
            side_effect=requests.exceptions.ConnectionError,
        ):
            # Should not raise
            self.agent._bookai_post_webhook(
                "https://bookai.test/webhooks/test",
                {"Authorization": "Bearer x"},
                {"test": True},
            )

    @mute_logger(WEBHOOK_LOGGER)
    def test_post_webhook_catches_timeout(self):
        with patch(
            "odoo.addons.pms_bookai.models.bookai_webhook_mixin." "requests.post",
            side_effect=requests.exceptions.Timeout,
        ):
            self.agent._bookai_post_webhook(
                "https://bookai.test/webhooks/test",
                {"Authorization": "Bearer x"},
                {"test": True},
            )

    def test_webhook_skip_fields_suppresses_write(self):
        server = self.env["bookai.mcp.server"].create(
            {
                "name": "Test MCP",
                "transport_type": "stdio",
                "command": "echo",
            }
        )
        with patch(
            "odoo.addons.pms_bookai.models.bookai_webhook_mixin." "requests.post"
        ) as mock_post:
            server.write({"connection_status": "connected"})
        mock_post.assert_not_called()

    def test_webhook_non_skip_fields_triggers_write(self):
        server = self.env["bookai.mcp.server"].create(
            {
                "name": "Test MCP",
                "transport_type": "stdio",
                "command": "echo",
            }
        )
        with patch(
            "odoo.addons.pms_bookai.models.bookai_webhook_mixin." "requests.post"
        ) as mock_post:
            server.write({"name": "Renamed MCP"})
        self.assertTrue(mock_post.called)
