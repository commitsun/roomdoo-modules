from odoo import api, models


class AccountPayment(models.Model):
    _name = "account.payment"
    _inherit = ["account.payment", "pms.notification.mixin"]

    @api.model_create_multi
    def create(self, vals_list):
        payments = super().create(vals_list)
        # Run event-based rules on create
        payments._pms_notification_run_event_rules(event_type="on_create")
        return payments

    def write(self, vals):
        res = super().write(vals)
        # Run event-based rules on write
        self._pms_notification_run_event_rules(event_type="on_write")
        return res
