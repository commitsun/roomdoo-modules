from odoo import models


class PmsReservation(models.Model):
    _inherit = "pms.reservation"

    def action_reservation_checkout(self):
        res = super().action_reservation_checkout()
        folios = self.mapped("folio_id")
        if folios:
            folios.sudo()._try_schedule_satisfaction_survey()
        return res
