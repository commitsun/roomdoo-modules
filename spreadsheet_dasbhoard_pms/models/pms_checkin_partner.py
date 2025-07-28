from odoo import fields, models, api
from dateutil.relativedelta import relativedelta

class PmsCheckinPartner(models.Model):

    _inherit = 'pms.checkin.partner'

    reservation_sale_channel_id = fields.Many2one('pms.sale.channel', related='reservation_id.sale_channel_origin_id', store=True)
    reservation_room_type_id = fields.Many2one('pms.room.type', related='reservation_id.room_type_id', store=True)
    age_on_checkin = fields.Integer(compute='_compute_age_on_checkin', store=True)

    @api.depends('birthdate_date', 'checkin')
    def _compute_age_on_checkin(self):
        for checkin_partner in self:
            if checkin_partner.birthdate_date and checkin_partner.checkin:
                checkin_partner.age_on_checkin = relativedelta(checkin_partner.checkin, checkin_partner.birthdate_date).years
            else:
                checkin_partner.age_on_checkin = 0
