from odoo import models

AEAT_TYPES_MAP = {
    "03": "passport",
    "05": "residence_card",
    "06": "other",
}


class PmsApiContactRouterHelper(models.AbstractModel):
    _inherit = "pms_api_contact.contact_fiscal_document_type_router.helper"

    def get_fiscal_document_types(self) -> list[str]:
        res = super().get_fiscal_document_types()
        res += ["passport", "residence_card", "other"]
        return res
