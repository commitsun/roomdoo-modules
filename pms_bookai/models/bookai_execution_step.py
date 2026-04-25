from odoo import fields, models


class BookaiExecutionStep(models.Model):
    _name = "bookai.execution.step"
    _description = "BooKAI Execution Step"
    _order = "sequence, id"

    execution_id = fields.Many2one(
        "bookai.execution",
        required=True,
        ondelete="cascade",
        string="Execution",
    )
    parent_step_id = fields.Many2one(
        "bookai.execution.step",
        ondelete="cascade",
        string="Parent Step",
    )
    child_step_ids = fields.One2many(
        "bookai.execution.step",
        "parent_step_id",
        string="Child Steps",
    )
    sequence = fields.Integer(default=10)

    # What happened
    step_type = fields.Selection(
        [
            ("tool_call", "Tool Call"),
            ("delegation", "Agent Delegation"),
            ("confirmation", "Confirmation Request"),
            ("escalation", "Escalation"),
            ("decision", "Decision"),
            ("error", "Error"),
        ],
        required=True,
    )

    # Agent context
    agent_id = fields.Many2one(
        "bookai.agent",
        string="Executing Agent",
    )
    effective_role = fields.Selection(
        [
            ("advisor", "Advisor"),
            ("assistant", "Assistant"),
            ("operator", "Operator"),
        ],
    )

    # Tool call details
    tool_id = fields.Many2one(
        "bookai.tool",
        string="Tool",
    )
    tool_name = fields.Char()
    tool_args = fields.Text(
        help="JSON arguments passed to the tool.",
    )
    tool_result = fields.Text(
        help="JSON result from the tool (full/debug only).",
    )

    # Delegation details
    delegated_agent_id = fields.Many2one(
        "bookai.agent",
        string="Delegated To",
    )

    # Confirmation details
    confirmation_summary = fields.Text(
        help="Summary shown to user before confirmation.",
    )
    confirmation_response = fields.Text(
        help="User's response to confirmation request.",
    )
    confirmed = fields.Boolean()

    # Result
    status = fields.Selection(
        [
            ("pending", "Pending"),
            ("success", "Success"),
            ("error", "Error"),
            ("rejected", "Rejected"),
            ("skipped", "Skipped"),
        ],
        default="pending",
    )
    error_message = fields.Text()
    timestamp = fields.Datetime(
        default=fields.Datetime.now,
    )
    description = fields.Text(
        help="Human-readable description of this step.",
    )
