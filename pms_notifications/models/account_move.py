from odoo import api, models


class AccountMove(models.Model):
    _name = "account.move"
    _inherit = ["account.move", "pms.notification.mixin"]

    @api.model_create_multi
    def create(self, vals_list):
        moves = super().create(vals_list)
        # Run event-based rules on create
        moves._pms_notification_run_event_rules(event_type="on_create")
        return moves

    def write(self, vals):
        res = super().write(vals)
        # Run event-based rules on write
        self._pms_notification_run_event_rules(event_type="on_write")
        return res
