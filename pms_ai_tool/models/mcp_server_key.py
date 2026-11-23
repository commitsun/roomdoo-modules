import json
import time

from odoo import _, models

PARAM_LOG_MAX_CHARS = "pms_ai_tool.log_max_chars"
DEFAULT_LOG_MAX_CHARS = 4000
CALL_START_CONTEXT_KEY = "pms_ai_call_start"
# Changing any of these must cut or restore access at once: the key lookup
# is cached and ai_oca_mcp only clears that cache when a key is expired.
ACCESS_FIELDS = {"state", "expiration_date", "hashed_key", "user_id", "server_id"}


class McpServerKey(models.Model):
    _inherit = "mcp.server.key"

    def write(self, vals):
        res = super().write(vals)
        if ACCESS_FIELDS & set(vals):
            self._pms_ai_clear_key_cache()
        return res

    def unlink(self):
        res = super().unlink()
        self._pms_ai_clear_key_cache()
        return res

    def _pms_ai_clear_key_cache(self):
        model = self.env["mcp.server.key"]
        model._get_mcp_server_by_key.clear_cache(model)

    def _tools_list(self, payload):
        response = super()._tools_list(payload)
        allowed = set(
            self.server_id.tool_ids.filtered(
                lambda tool: tool._pms_ai_is_allowed()
            ).mapped("name")
        )
        response["result"]["tools"] = [
            tool for tool in response["result"]["tools"] if tool["name"] in allowed
        ]
        return response

    def _tools_call(self, payload):
        key = self.with_context(**{CALL_START_CONTEXT_KEY: time.monotonic()})
        return super(McpServerKey, key)._tools_call(payload)

    def _add_log(self, **log_vals):
        start = self.env.context.get(CALL_START_CONTEXT_KEY)
        if start is not None:
            log_vals["duration_ms"] = int((time.monotonic() - start) * 1000)
        tool_name = self._pms_ai_requested_tool_name(log_vals.get("request"))
        log_vals["tool_name"] = tool_name
        tool = self.server_id.tool_ids.filtered(lambda t: t.name == tool_name)[:1]
        guard = tool._pms_ai_guard() if tool else None
        if guard and not guard["log_result"] and log_vals.get("response"):
            log_vals["response"] = self._pms_ai_strip_result(log_vals["response"])
        limit = int(
            self.env["ir.config_parameter"]
            .sudo()
            .get_param(PARAM_LOG_MAX_CHARS, DEFAULT_LOG_MAX_CHARS)
        )
        for field_name in ("request", "response", "error"):
            text = log_vals.get(field_name)
            if text and len(text) > limit:
                log_vals[field_name] = text[: limit - 1] + "…"
        return super()._add_log(**log_vals)

    def _pms_ai_requested_tool_name(self, request):
        try:
            name = json.loads(request or "{}").get("name")
        except (ValueError, AttributeError):
            return False
        return name if isinstance(name, str) else False

    def _pms_ai_strip_result(self, response):
        try:
            envelope = json.loads(response)
        except ValueError:
            return response
        if isinstance(envelope, dict) and "result" in envelope:
            envelope["result"] = _("(not logged: the tool returns personal data)")
        return json.dumps(envelope)
