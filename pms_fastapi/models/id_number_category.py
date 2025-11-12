from odoo import fields, models


class ResPartnerIdCategory(models.Model):
    _inherit = "res.partner.id_category"

    validable_document = fields.Boolean(
        string="Is Validable Document", compute="_compute_validable_document"
    )

    def _compute_validable_document(self):
        for record in self:
            record.validable_document = bool(record.validation_code)
