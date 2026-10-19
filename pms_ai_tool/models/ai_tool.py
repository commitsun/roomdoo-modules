from odoo import _, fields, models
from odoo.exceptions import AccessError

from ..tools import TOOL_CONTEXT_KEY, get_guard


class AiTool(models.Model):
    _inherit = "ai.tool"

    guard_mode = fields.Selection(
        [("read", "Read"), ("write", "Write"), ("unguarded", "Not guarded")],
        compute="_compute_guard_mode",
        help="Read tools run in a read-only transaction. Write tools need a "
        "preview and an explicit confirmation.",
    )
    allowed_user_ids = fields.Many2many(
        "res.users",
        string="Allowed users",
        groups="base.group_system",
        help="Only these users can run the tool. Empty means any user whose "
        "MCP server includes it, except for write tools, which always need "
        "an explicit list.",
    )
    write_enabled = fields.Boolean(
        groups="base.group_system",
        help="Allow confirmed changes. The system parameter "
        "pms_ai_tool.write_enabled must be enabled as well.",
    )

    def _compute_guard_mode(self):
        for tool in self:
            guard = tool._pms_ai_guard()
            if not guard:
                tool.guard_mode = "unguarded"
            else:
                tool.guard_mode = "write" if guard["write"] else "read"

    def _pms_ai_guard(self):
        self.ensure_one()
        model = self.sudo().model_id.model
        if not model or model not in self.env:
            return None
        return get_guard(getattr(self.env[model], self.function_name or "", None))

    def _pms_ai_is_allowed(self):
        self.ensure_one()
        guard = self._pms_ai_guard()
        allowed_users = self.sudo().allowed_user_ids
        if guard and guard["write"] and not allowed_users:
            return False
        return not allowed_users or self.env.user in allowed_users

    def _execute_tool(self, *args, record=None, **kwargs):
        self.ensure_one()
        if not self._pms_ai_is_allowed():
            raise AccessError(_("You are not allowed to use the tool %s.") % self.name)
        tool = self.with_context(**{TOOL_CONTEXT_KEY: self.id})
        return super(AiTool, tool)._execute_tool(*args, record=record, **kwargs)

    def _get_tool_definition(self):
        definition = super()._get_tool_definition()
        # MCP clients reject a non-string description.
        definition["description"] = definition.get("description") or ""
        guard = self._pms_ai_guard()
        if guard:
            definition["annotations"] = {
                "readOnlyHint": not guard["write"],
                "destructiveHint": guard["write"],
                "idempotentHint": not guard["write"],
                "openWorldHint": False,
            }
        return definition
