from fastapi import HTTPException

from odoo import api, models

from odoo.addons.l10n_es_aeat_partner_identification.models.res_partner import (
    AEAT_TYPES_ID_CATEGORY_MAP,
)
from odoo.addons.pms_fastapi.schemas.contact import (
    ContactInsert,
    ContactUpdate,
)


class PmsApiContactRouterHelper(models.AbstractModel):
    _inherit = "pms_api_contact.contact_router.helper"

    def create_contact(self, data: ContactInsert):
        res = super().create_contact(data)
        if data.fiscalIdNumberType:
            if data.fiscalIdNumberType != "vat":
                res["aeat_identification_type"] = data.fiscalIdNumberType
                res["aeat_identification"] = data.fiscalIdNumber
            else:
                res["vat"] = data.fiscalIdNumber
        return res

    def update_contact(self, data: ContactUpdate, contact_id: int):
        res = super().update_contact(data, contact_id)
        partner = self.env["res.partner"].browse(contact_id)
        if data.fiscalIdNumber:
            if (
                data.fiscalIdNumberType
                and data.fiscalIdNumberType != "vat"
                or not data.fiscalIdNumberType
                and partner.aeat_identification_type
            ):
                partner.aeat_identification = data.fiscalIdNumber
                partner.vat = False
            else:
                partner.vat = data.fiscalIdNumber
                partner.aeat_identification = False
        if data.fiscalIdNumberType:
            if data.fiscalIdNumberType != "vat":
                odoo_fiscal_type = [
                    key
                    for key, value in AEAT_TYPES_ID_CATEGORY_MAP.items()
                    if value == data.fiscalIdNumberType
                ]
                if not odoo_fiscal_type:
                    raise HTTPException(
                        status_code=404,
                        detail=f"fiscalIdNumberType not \
                            found {data.fiscalIdNumberType}",
                    )
                partner.aeat_identification_type = (
                    odoo_fiscal_type and odoo_fiscal_type[0]
                )
            else:
                partner.aeat_identification_type = False
        return res

    @api.model
    def extra_features(self):
        res = super().extra_features()
        res.append("comercial_name")
        return res
