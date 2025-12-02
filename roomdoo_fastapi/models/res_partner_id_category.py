from odoo import fields, models


class ResPartnerIdCategory(models.Model):
    _inherit = "res.partner.id_category"

    short_code = fields.Char(
        string="Short Code", help="A short code for the ID category", translate=True
    )
