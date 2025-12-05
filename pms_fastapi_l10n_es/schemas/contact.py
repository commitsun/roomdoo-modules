from pydantic import Field

from odoo.addons.l10n_es_aeat_partner_identification.models.res_partner import (
    AEAT_TYPES_ID_CATEGORY_MAP,
)
from odoo.addons.pms_fastapi.schemas import contact


class contactDetailFiscalDocument(contact.ContactDetail, extends=True):
    comercial: str = Field("", alias="comercial")

    @classmethod
    def from_res_partner(cls, partner):
        obj = super().from_res_partner(partner)
        if partner.aeat_identification_type:
            obj.fiscalIdNumberType = AEAT_TYPES_ID_CATEGORY_MAP[
                partner.aeat_identification_type
            ]
            obj.fiscalIdNumber = partner.aeat_identification
        return obj


class ContactInsertComercialName(contact.ContactInsert, extends=True):
    comercial: str = Field("", alias="comercial")
