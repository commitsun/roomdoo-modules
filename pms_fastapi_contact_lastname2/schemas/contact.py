from pydantic import Field

from odoo.addons.pms_fastapi.schemas import contact
from odoo.addons.pms_fastapi.schemas.base import StrippedText


class ContactDetailLastname2(contact.ContactDetail, extends=True):
    lastname2: str = Field(
        "",
        description="Second last name. Only a person has last names; it is "
        "always empty for a company.",
    )

    @classmethod
    def _name_from_res_partner(cls, partner) -> dict:
        res = super()._name_from_res_partner(partner)
        res["lastname2"] = "" if partner.is_company else (partner.lastname2 or "")
        return res


class ContactInsert(contact.ContactInsert, extends=True):
    lastname2: StrippedText = Field(
        "",
        description="Second last name. Only accepted for a person; sending a "
        "value for a company is rejected.",
    )

    @classmethod
    def surname_fields(cls) -> tuple:
        return super().surname_fields() + ("lastname2",)
