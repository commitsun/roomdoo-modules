from odoo import api, fields, models


class BookaiAgentKbBinding(models.Model):
    _name = "bookai.agent.kb.binding"
    _description = "BooKAI Agent KB Document Binding"
    _order = "document_id, id"

    agent_id = fields.Many2one(
        "bookai.agent",
        required=True,
        ondelete="cascade",
        string="Agent",
    )
    document_id = fields.Many2one(
        "bookai.kb.document",
        required=True,
        ondelete="cascade",
        string="Document",
        domain="[('active', '=', True)]",
    )
    description_override = fields.Text(
        help="Overrides the global document description for "
        "this agent. Leave empty to use the global description.",
    )
    active = fields.Boolean(default=True)

    # Related fields for display
    doc_type = fields.Selection(
        related="document_id.doc_type",
        readonly=True,
    )
    source_type = fields.Selection(
        related="document_id.source_type",
        readonly=True,
    )
    document_description = fields.Char(
        related="document_id.description",
        string="Global Description",
        readonly=True,
    )

    _sql_constraints = [
        (
            "agent_document_unique",
            "unique(agent_id, document_id)",
            "A document can only be bound once to the same agent.",
        ),
    ]

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
