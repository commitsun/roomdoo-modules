from odoo import models


class ResPartnerIdNumber(models.Model):
    _inherit = "res.partner.id_number"

    def _set_partner_passport(self):
        for record in self:
            record.partner_id.write(
                {"aeat_identification_type": "03", "aeat_identification": record.name}
            )

    def _set_partner_residential_certificate(self):
        for record in self:
            record.partner_id.write(
                {"aeat_identification_type": "05", "aeat_identification": record.name}
            )

    def _set_partner_another_document(self):
        for record in self:
            record.partner_id.write(
                {"aeat_identification_type": "06", "aeat_identification": record.name}
            )

    def _get_duplicate_passport(self, name, category_id, country_id, partner_id=None):
        return self._get_duplicate_aeat(name, "03", partner_id)

    def _get_duplicate_residential_certificate(
        self, name, category_id, country_id, partner_id=None
    ):
        return self._get_duplicate_aeat(name, "05", partner_id)

    def _get_duplicate_another_document(
        self, name, category_id, country_id, partner_id=None
    ):
        return self._get_duplicate_aeat(name, "06", partner_id)

    def _get_duplicate_aeat(self, name, aeat_identification_type, partner_id=None):
        duplicate_another_document_domain = [
            ("aeat_identification", "=", name),
            ("aeat_identification_type", "=", aeat_identification_type),
            ("parent_id", "=", False),
        ]
        if partner_id:
            duplicate_another_document_domain.append(("id", "!=", partner_id.id))
        duplicate_another_document_partner = self.env["res.partner"].search(
            duplicate_another_document_domain
        )
        if duplicate_another_document_partner:
            return duplicate_another_document_partner
        return None
