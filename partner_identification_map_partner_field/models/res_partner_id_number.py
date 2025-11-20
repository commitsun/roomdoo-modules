from odoo import models


class ResPartnerIdNumber(models.Model):
    _inherit = "res.partner.id_number"

    def set_partner_id_field(self):
        for record in self:
            if record.category_id.partner_map_field:
                partner_map_function = (
                    "_set_partner_" + record.category_id.partner_map_field
                )
                partner_map_callable = getattr(record, partner_map_function, None)
                if partner_map_callable:
                    return partner_map_callable()
        return True

    def _set_partner_vat(self):
        for record in self:
            vat = record.name
            if record.partner_id.country_id != record.country_id:
                vat = record.country_id.code + vat
            record.partner_id.vat = vat
        return True
