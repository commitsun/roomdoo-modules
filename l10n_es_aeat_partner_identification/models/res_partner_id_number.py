from odoo import models


class ResPartnerIdNumber(models.Model):
    _inherit = "res.partner.id_number"

    def set_partner_id_vatnumber(self):
        for record in self:
            if record.category_id.aeat_identification_type in ["02", "04"]:
                record.partner_id.vat = record.name
            elif record.category_id.aeat_identification_type in ["03", "05", "06"]:
                record.partner_id.aeat_identification = record.name
                record.partner_id.aeat_identification_type = (
                    record.category_id.aeat_identification_type
                )
        return True
