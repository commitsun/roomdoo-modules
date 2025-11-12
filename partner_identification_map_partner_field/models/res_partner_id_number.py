from odoo import models


class ResPartnerIdNumber(models.Model):
    _inherit = "res.partner.id_number"

    def set_partner_id_field(self):
        for record in self:
            if record.id_category_id.partner_map_field:
                partner_map_function = (
                    "_set_partner_" + record.id_category_id.partner_map_field
                )
                partner_map_callable = getattr(record, partner_map_function, None)
                if partner_map_callable:
                    return partner_map_callable()
        return True

    def _set_partner_vat(self):
        for record in self:
            record.partner_id.vat = record.name
        return True
