from odoo import models

from odoo.addons.l10n_es_aeat_partner_identification.models.res_partner import (
    AEAT_TYPES_ID_CATEGORY_MAP,
)


class PmsApiContactIdNumberRouterHelper(models.AbstractModel):
    _inherit = "pms_api_contact.contact_id_number_router.helper"

    def get_duplicate_fiscal_number(
        self, fiscal_number: str, document_type: str, country_id: int | None = None
    ):
        if document_type in list(AEAT_TYPES_ID_CATEGORY_MAP.values()):
            aeat_identification_type = [
                key
                for key, value in AEAT_TYPES_ID_CATEGORY_MAP.items()
                if value == document_type
            ][0]
            return (
                self.env["res.partner"]
                .sudo()
                .get_duplicate_aeat(aeat_identification_type, fiscal_number)
            )
        else:
            return super().get_duplicate_fiscal_number(
                fiscal_number, document_type, country_id
            )
