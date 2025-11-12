from odoo import fields, models


class ResPartnerIdCategory(models.Model):
    _inherit = "res.partner.id_category"

    partner_map_field = fields.Selection(
        selection_add=[
            ("passport", "Passport"),
            ("residential_certificate", "Residential certificate"),
            ("another_document", "Another document"),
        ]
    )
