from odoo import fields, models


class BookaiAgentAuditLog(models.Model):
    _name = "bookai.agent.audit.log"
    _description = "BooKAI Agent Audit Log"
    _order = "timestamp desc"

    agent_id = fields.Many2one(
        "bookai.agent",
        required=True,
        ondelete="cascade",
        string="Agent",
    )
    user_id = fields.Many2one(
        "res.users",
        string="Requested By",
    )
    pms_property_id = fields.Many2one(
        "pms.property",
        string="Property",
    )
    timestamp = fields.Datetime(
        default=fields.Datetime.now,
        required=True,
    )
    operation = fields.Selection(
        [
            ("read", "Read"),
            ("create", "Create"),
            ("write", "Write"),
            ("unlink", "Delete"),
            ("call", "Method Call"),
        ],
        required=True,
    )
    model_name = fields.Char(string="Model")
    method_name = fields.Char(string="Method")
    record_ids = fields.Char(
        help="JSON list of affected record IDs.",
    )
    args_summary = fields.Text(
        string="Summary",
        help="Human-readable summary of the operation.",
    )
    confirmation_summary = fields.Text(
        string="Confirmation Summary",
        help="Human-readable summary shown to the guest "
        "before confirmation. Stored for audit trail.",
    )
    confirmed_by = fields.Text(
        help="Who confirmed and their exact message, e.g. "
        '"guest: Sí, confirmo la reserva".',
    )
    conversation_id = fields.Char(index=True)
    status = fields.Selection(
        [
            ("pending", "Pending"),
            ("confirmed", "Confirmed"),
            ("success", "Success"),
            ("error", "Error"),
            ("rejected", "Rejected"),
        ],
    )
    error_message = fields.Text()
