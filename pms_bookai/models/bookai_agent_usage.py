from odoo import fields, models


class BookaiAgentUsage(models.Model):
    _name = "bookai.agent.usage"
    _description = "BooKAI Agent Usage"
    _order = "timestamp desc"

    pms_property_id = fields.Many2one(
        "pms.property",
        required=True,
        ondelete="cascade",
        string="Property",
    )
    agent_id = fields.Many2one(
        "bookai.agent",
        required=True,
        ondelete="cascade",
        string="Agent",
    )
    llm_account_id = fields.Many2one(
        "bookai.llm.account",
        required=True,
        ondelete="cascade",
        string="LLM Account",
    )
    tokens_in = fields.Integer(default=0)
    tokens_out = fields.Integer(default=0)
    cost_usd = fields.Float(
        string="Cost (USD)",
        digits=(10, 6),
        help=(
            "Estimated cost in USD calculated by BookAI " "using litellm pricing data."
        ),
    )
    call_count = fields.Integer(
        string="Call Count",
        default=1,
        help="Number of LLM calls accumulated in this record. "
        "BookAI aggregates usage per conversation per agent.",
    )
    whisper_seconds = fields.Float(
        string="Whisper Duration (s)",
        digits=(10, 1),
        help="Audio transcription duration in seconds.",
    )
    whisper_cost_usd = fields.Float(
        string="Whisper Cost (USD)",
        digits=(10, 6),
    )
    vision_calls = fields.Integer(
        string="Vision Calls",
        help="Number of image descriptions.",
    )
    vision_cost_usd = fields.Float(
        string="Vision Cost (USD)",
        digits=(10, 6),
    )
    total_cost_usd = fields.Float(
        string="Total Cost (USD)",
        digits=(10, 6),
        help="Sum of LLM + Whisper + Vision costs.",
    )
    model = fields.Char(string="Model Used")
    conversation_id = fields.Char(index=True)
    timestamp = fields.Datetime(default=fields.Datetime.now, required=True)
    status = fields.Selection(
        [
            ("success", "Success"),
            ("error", "Error"),
            ("escalated", "Escalated"),
        ],
    )
    error_message = fields.Text()
