from odoo import fields, models, api


class PmsReservationLine(models.Model):
    _inherit = 'pms.reservation.line'

    stay_number = fields.Integer(compute='_compute_stay_number', store=True)


    @api.depends('reservation_id', 'reservation_id.checkin_partner_ids', 'reservation_id.checkin_partner_ids.state')
    def _compute_stay_number(self):
        for line in self:
            line.stay_number = len(line.reservation_id.checkin_partner_ids.filtered(lambda r: r.state != 'dummy'))
