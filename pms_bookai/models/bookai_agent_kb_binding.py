from odoo import fields, models


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
