from odoo import models


class PmsReservationLine(models.Model):
    _inherit = "pms.reservation.line"

    def write(self, vals):
        # Listen at line level to catch API writes that don't go through
        # ``pms.reservation.write`` (direct ``line.write({'room_id': X})``).
        result = super().write(vals)
        if "room_id" in vals:
            self.mapped("reservation_id")._enqueue_lock_sync()
        return result
