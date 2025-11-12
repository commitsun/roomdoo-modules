from odoo import models

from odoo.addons.l10n_es_aeat_partner_identification.models.res_partner import (
    AEAT_TYPES_ID_CATEGORY_MAP,
)


class PmsApiContactRouterHelper(models.AbstractModel):
    _inherit = "pms_api_contact.contact_fiscal_document_type_router.helper"

    def get_fiscal_document_types(self) -> list[str]:
        res = super().get_fiscal_document_types()
        res += list(AEAT_TYPES_ID_CATEGORY_MAP.values())
        return res
