from odoo import fields, models


class BookaiAgentDelegation(models.Model):
    _name = "bookai.agent.delegation"
    _description = "BooKAI Agent Delegation"
    _order = "delegate_agent_id, id"

    agent_id = fields.Many2one(
        "bookai.agent",
        required=True,
        ondelete="cascade",
        string="Agent",
    )
    delegate_agent_id = fields.Many2one(
        "bookai.agent",
        required=True,
        ondelete="cascade",
        string="Delegate",
        domain="[('active', '=', True)]",
    )
    description_override = fields.Text(
        help="Overrides the global delegate description for "
        "this agent. Leave empty to use the global description.",
    )
    active = fields.Boolean(default=True)

    # Related fields for display
    delegate_caller_type = fields.Selection(
        related="delegate_agent_id.caller_type",
        readonly=True,
    )
    delegate_description = fields.Text(
        related="delegate_agent_id.description",
        string="Global Description",
        readonly=True,
    )

    _sql_constraints = [
        (
            "agent_delegate_unique",
            "unique(agent_id, delegate_agent_id)",
            "A delegate can only be bound once to the same agent.",
        ),
        (
            "agent_delegate_no_self",
            "CHECK(agent_id <> delegate_agent_id)",
            "An agent cannot delegate to itself.",
        ),
    ]
