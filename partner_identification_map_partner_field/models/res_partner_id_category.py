from odoo import fields, models


class ResPartnerIdCategory(models.Model):
    _inherit = "res.partner.id_category"

    partner_map_field = fields.Selection(
        [("vat", "VAT")], string="Partner Field to map"
    )
