from pydantic import Field

from odoo.addons.pms_fastapi.schemas import contact

from ..routers.contact_fiscal_document_type import AEAT_TYPES_MAP


class contactDetailFiscalDocument(contact.ContactDetail, extends=True):
    comercial_name: str = Field("", alias="comercial")

    @classmethod
    def from_res_partner(cls, partner):
        obj = super().from_res_partner(partner)
        if partner.aeat_identification_type:
            obj.fiscalIdNumberType = AEAT_TYPES_MAP[partner.aeat_identification_type]
            obj.fiscalIdNumber = partner.aeat_identification
        return obj


class ContactInsertComercialName(contact.ContactInsert, extends=True):
    comercial_name: str = Field("", alias="comercial")
