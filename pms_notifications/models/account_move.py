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
        changed_fields = set(vals)
        pre_domain_matches = self._pms_notification_prepare_pre_domain_matches(
            event_type="on_write",
            changed_fields=changed_fields,
        )
        res = super().write(vals)
        # Run event-based rules on write
        self._pms_notification_run_event_rules(
            event_type="on_write",
            changed_fields=changed_fields,
            pre_domain_matches=pre_domain_matches,
        )
        return res
