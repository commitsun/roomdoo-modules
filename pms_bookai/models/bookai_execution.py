from odoo import fields, models


class BookaiExecution(models.Model):
    _name = "bookai.execution"
    _description = "BooKAI Execution"
    _order = "start_time desc"

    agent_id = fields.Many2one(
        "bookai.agent",
        required=True,
        ondelete="cascade",
        string="Root Agent",
    )
    pms_property_id = fields.Many2one(
        "pms.property",
        string="Property",
    )
    conversation_id = fields.Char(index=True)
    caller_info = fields.Char(
        help="Identifier of the caller (phone, user, system).",
    )

    # Timing
    start_time = fields.Datetime(
        default=fields.Datetime.now,
        required=True,
    )
    end_time = fields.Datetime()
    duration_seconds = fields.Float(
        compute="_compute_duration",
        store=True,
    )

    # Effective policy (as resolved at start)
    effective_role = fields.Selection(
        [
            ("advisor", "Advisor"),
            ("assistant", "Assistant"),
            ("operator", "Operator"),
        ],
    )
    effective_confirmation = fields.Selection(
        [
            ("always", "Always"),
            ("sensitive", "Sensitive"),
            ("irreversible", "Irreversible"),
            ("never", "Never"),
        ],
    )
    effective_log_level = fields.Selection(
        [
            ("basic", "Basic"),
            ("full", "Full"),
            ("debug", "Debug"),
        ],
    )

    # Status
    state = fields.Selection(
        [
            ("running", "Running"),
            ("completed", "Completed"),
            ("error", "Error"),
            ("cancelled", "Cancelled"),
        ],
        default="running",
        required=True,
    )
    result_summary = fields.Text()
    error_message = fields.Text()

    # Steps
    step_ids = fields.One2many(
        "bookai.execution.step",
        "execution_id",
        string="Steps",
    )
    step_count = fields.Integer(
        compute="_compute_step_stats",
        store=True,
    )
    confirmation_count = fields.Integer(
        compute="_compute_step_stats",
        store=True,
    )

    def _compute_duration(self):
        for rec in self:
            if rec.start_time and rec.end_time:
                delta = rec.end_time - rec.start_time
                rec.duration_seconds = delta.total_seconds()
            else:
                rec.duration_seconds = 0.0

    def _compute_step_stats(self):
        for rec in self:
            steps = rec.step_ids
            rec.step_count = len(steps)
            rec.confirmation_count = len(
                steps.filtered(lambda s: s.step_type == "confirmation")
            )
