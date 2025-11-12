from odoo import _, api, models
from odoo.exceptions import ValidationError


class ResPartnerIdNumber(models.Model):
    _inherit = "res.partner.id_number"

    @api.model
    def get_duplicate(self, name, category_id, country_id, partner_id=None):
        duplicate_id_number_domain = [
            ("name", "=ilike", name),
            ("country_id", "=", country_id.id),
            ("category_id", "=", category_id.id),
        ]
        if partner_id:
            duplicate_id_number_domain.append(("partner_id", "!=", partner_id.id))
        duplicate_id_number = self.search(duplicate_id_number_domain)
        if duplicate_id_number:
            return duplicate_id_number.partner_id
        if category_id.partner_map_field:
            partner_map_function = "_get_duplicate_" + category_id.partner_map_field
            partner_map_callable = getattr(self, partner_map_function, None)
            if partner_map_callable:
                return partner_map_callable(name, category_id, country_id, partner_id)

    @api.model
    def _get_duplicate_vat(self, name, category_id, country_id, partner_id=None):
        vat = name
        vat_with_country = country_id.code + vat
        duplicate_vat_domain = [
            ("parent_id", "=", False),
            "|",
            ("vat", "=ilike", vat),
            ("vat", "=ilike", vat_with_country),
        ]
        if partner_id:
            duplicate_vat_domain.append(("id", "!=", partner_id.id))
        duplicate_vat_partner = self.env["res.partner"].search(duplicate_vat_domain)
        if duplicate_vat_partner:
            return duplicate_vat_partner
        return None

    @api.constrains("name", "category_id", "partner_id")
    def _check_id_number_unique(self):
        for record in self:
            if not record.name:
                continue
            if record.get_duplicate(
                record.name, record.category_id, record.country_id, record.partner_id
            ):
                raise ValidationError(
                    _("The identification number %s already exists in another partner.")
                    % record.name
                )
