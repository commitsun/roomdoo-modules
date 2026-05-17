from odoo import api, fields, models


class BookaiAgentToolBinding(models.Model):
    _name = "bookai.agent.tool.binding"
    _description = "BooKAI Agent Tool Binding"
    _order = "tool_id, id"

    agent_id = fields.Many2one(
        "bookai.agent",
        required=True,
        ondelete="cascade",
        string="Agent",
    )
    tool_id = fields.Many2one(
        "bookai.tool",
        required=True,
        ondelete="restrict",
        string="Tool",
        domain="[('active', '=', True)]",
    )
    description_override = fields.Text(
        help="Overrides the global tool description for this "
        "agent. Leave empty to use the global description.",
    )
    requires_confirm = fields.Boolean(
        help="Legacy. Use action_sensitivity_override.",
    )
    action_sensitivity_override = fields.Selection(
        [
            ("none", "None"),
            ("sensitive", "Sensitive"),
            ("irreversible", "Irreversible"),
        ],
        help="Override the tool's global sensitivity for "
        "this agent. Empty = use global.",
    )
    active = fields.Boolean(default=True)

    # Related fields for display
    tool_type = fields.Selection(
        related="tool_id.tool_type",
        readonly=True,
    )
    tool_description = fields.Text(
        related="tool_id.description",
        string="Global Description",
        readonly=True,
    )

    # ------------------------------------------------------------------
    # CRUD — fan out to parent agent's webhook so BookAI reloads.
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._notify_agent_webhook()
        return records

    def write(self, vals):
        if "agent_id" in vals:
            old_agents = self.mapped("agent_id")
            result = super().write(vals)
            new_agents = self.mapped("agent_id")
            (old_agents | new_agents)._notify_bookai_webhook("upsert")
        else:
            result = super().write(vals)
            self._notify_agent_webhook()
        return result

    def unlink(self):
        agents = self.mapped("agent_id")
        result = super().unlink()
        agents._notify_bookai_webhook("upsert")
        return result

    def _notify_agent_webhook(self):
        agents = self.mapped("agent_id")
        if agents:
            agents._notify_bookai_webhook("upsert")
