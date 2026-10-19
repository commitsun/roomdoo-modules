from datetime import timedelta

from odoo import api, fields, models

PARAM_RETENTION_DAYS = "pms_ai_tool.log_retention_days"
DEFAULT_RETENTION_DAYS = 90


class McpServerLog(models.Model):
    _inherit = "mcp.server.log"
    _order = "date desc, id desc"

    tool_name = fields.Char(index=True, readonly=True)
    user_id = fields.Many2one(
        related="server_key_id.user_id",
        store=True,
        index=True,
        string="User",
    )
    duration_ms = fields.Integer(string="Duration (ms)", readonly=True)

    @api.model
    def _cron_purge_old_logs(self):
        days = int(
            self.env["ir.config_parameter"]
            .sudo()
            .get_param(PARAM_RETENTION_DAYS, DEFAULT_RETENTION_DAYS)
        )
        limit = fields.Datetime.now() - timedelta(days=days)
        self.sudo().search([("date", "<", limit)]).unlink()
        self.env["pms.ai.confirmation"]._purge()
