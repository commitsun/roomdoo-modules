from odoo import api, models


class PmsReservation(models.Model):
    _name = "pms.reservation"
    _inherit = ["pms.reservation", "pms.notification.mixin"]

    @api.model_create_multi
    def create(self, vals_list):
        reservations = super().create(vals_list)
        # Run event-based rules on create
        reservations._pms_notification_run_event_rules(event_type="on_create")
        return reservations

    def write(self, vals):
        res = super().write(vals)
        # Run event-based rules on write
        self._pms_notification_run_event_rules(event_type="on_write")
        return res

    def action_open_send_notification_wizard(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Send Notification",
            "res_model": "pms.notification.manual.send.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_res_model": self._name,
                "default_res_id": self.id,
            },
        }
