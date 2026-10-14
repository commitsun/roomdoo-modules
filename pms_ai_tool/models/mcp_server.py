from odoo import models


class McpServer(models.Model):
    _inherit = "mcp.server"

    def write(self, vals):
        res = super().write(vals)
        if {"active", "key"} & set(vals):
            self.env["mcp.server.key"]._pms_ai_clear_key_cache()
        return res

    def unlink(self):
        res = super().unlink()
        self.env["mcp.server.key"]._pms_ai_clear_key_cache()
        return res
