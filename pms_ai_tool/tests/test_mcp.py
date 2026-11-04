import json
from datetime import timedelta

from odoo import fields
from odoo.tests.common import HttpCase, tagged
from odoo.tools import mute_logger

from ..models.mcp_server_key import PARAM_LOG_MAX_CHARS
from .common import PmsAiToolTestMixin


@tagged("post_install", "-at_install")
class TestMcp(PmsAiToolTestMixin, HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_ai_tools()
        cls.server = cls.env["mcp.server"].create(
            {
                "name": "AI test server",
                "tool_ids": [(6, 0, [tool.id for tool in cls.tools.values()])],
            }
        )
        wizard = (
            cls.env["mcp.server.key.add"]
            .with_context(default_server_id=cls.server.id)
            .create({"name": "AI test key", "user_id": cls.user_a.id})
        )
        wizard.generate_key()
        cls.key = wizard.key_id
        cls.secret = wizard.key

    def setUp(self):
        super().setUp()
        # Bind the requests to the test database even when the server
        # holds several of them.
        self.authenticate(None, None)

    def _post(self, method, params=None):
        payload = {"jsonrpc": "2.0", "id": "1", "method": method}
        if params is not None:
            payload["params"] = params
        response = self.url_open(
            f"/mcp/{self.server.key}",
            data=json.dumps(payload),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.secret}",
            },
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def _call(self, name, **arguments):
        return self._post("tools/call", {"name": name, "arguments": arguments})

    def _last_log(self):
        return self.env["mcp.server.log"].search(
            [("server_id", "=", self.server.id)], limit=1
        )

    def test_tools_list_hides_tools_of_other_users(self):
        self.tools["echo"].allowed_user_ids = self.user_b
        names = {
            tool["name"]: tool for tool in self._post("tools/list")["result"]["tools"]
        }
        self.assertNotIn("echo", names)
        self.assertIn("set_ref", names)
        self.assertTrue(names["set_ref"]["annotations"]["destructiveHint"])
        self.assertTrue(names["slow"]["annotations"]["readOnlyHint"])

    def test_call_is_logged_with_user_tool_and_duration(self):
        response = self._call("echo", date_from="2026-09-21")
        envelope = response["result"]["structuredContent"]
        self.assertEqual(envelope["environment"], "test")
        self.assertEqual(json.loads(response["result"]["content"][0]["text"]), envelope)
        log = self._last_log()
        self.assertEqual(log.tool_name, "echo")
        self.assertEqual(log.user_id, self.user_a)
        self.assertGreaterEqual(log.duration_ms, 0)
        self.assertIn('"environment": "test"', log.response)

    def test_log_is_truncated(self):
        self.env["ir.config_parameter"].sudo().set_param(PARAM_LOG_MAX_CHARS, "40")
        self._call("echo", date_from="2026-09-21")
        self.assertEqual(len(self._last_log().response), 40)

    def test_personal_results_are_not_logged(self):
        response = self._call("personal")
        self.assertEqual(
            response["result"]["structuredContent"]["result"], {"name": "Jane Doe"}
        )
        log = self._last_log()
        self.assertNotIn("Jane Doe", log.response)
        self.assertIn("One employee", log.response)

    def test_errors_are_plain_and_logged(self):
        with mute_logger("odoo.addons.pms_ai_tool.tools"):
            response = self._call("crash")
        self.assertIn("Internal error", response["error"]["message"])
        self.assertNotIn("Traceback", response["error"]["message"])
        self.assertIn("Internal error", self._last_log().error)

    def test_read_tool_cannot_write_over_http(self):
        with mute_logger("odoo.addons.pms_ai_tool.tools", "odoo.sql_db"):
            response = self._call("sneaky_write", partner_id=self.partner.id, via="sql")
        self.assertIn("nothing was saved", response["error"]["message"])
        self.partner.invalidate_recordset(["ref"])
        self.assertEqual(self.partner.ref, "orig")

    def test_write_over_http(self):
        preview = self._call("set_ref", partner_id=self.partner.id, new_ref="new")
        token = preview["result"]["structuredContent"]["confirmation"][
            "confirmation_token"
        ]
        applied = self._call(
            "set_ref",
            partner_id=self.partner.id,
            new_ref="new",
            confirm=True,
            confirmation_token=token,
        )
        self.assertEqual(
            applied["result"]["structuredContent"]["result"], {"done": True}
        )
        self.partner.invalidate_recordset(["ref"])
        self.assertEqual(self.partner.ref, "new")

    def _assert_cut(self):
        response = self._post("tools/list")
        self.assertEqual(response["error"]["message"], "Connection failed")

    def test_archiving_the_server_cuts_access_at_once(self):
        self.assertIn("result", self._post("tools/list"))
        self.server.active = False
        self._assert_cut()

    def test_expiring_the_key_cuts_access_at_once(self):
        self.assertIn("result", self._post("tools/list"))
        self.key.expiration_date = fields.Datetime.now() - timedelta(minutes=1)
        self._assert_cut()

    def test_deleting_the_key_cuts_access_at_once(self):
        self.assertIn("result", self._post("tools/list"))
        self.key.unlink()
        self._assert_cut()

    def test_old_logs_are_purged(self):
        self._call("echo", date_from="2026-09-21")
        recent = self._last_log()
        old = recent.copy({"date": fields.Datetime.now() - timedelta(days=91)})
        self.env["mcp.server.log"]._cron_purge_old_logs()
        self.assertTrue(recent.exists())
        self.assertFalse(old.exists())
