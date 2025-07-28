from odoo import fields, models


class PmsServiceLine(models.Model):
    _inherit = 'pms.service.line'

    reservation_sale_channel_id = fields.Many2one('pms.sale.channel', related='reservation_id.sale_channel_origin_id', store=True)
