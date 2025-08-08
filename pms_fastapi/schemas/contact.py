from enum import Enum

from fastapi import Query

from odoo import api

from .base import PmsBaseModel
from .country import CountryId


class ContactOrderField(str, Enum):
    name = "name"
    country = "country"
    type = "type"


CONTACT_ORDER_MAPPING = {
    "name": "name",
    "country": "country_id",
    "type": "pms_partner_type",
}


class ContactType(str, Enum):
    customer = "customer"
    supplier = "supplier"
    guest = "guest"
    agency = "agency"


class PhoneType(str, Enum):
    phone = "phone"
    mobile = "mobile"


class Phone(PmsBaseModel):
    type: PhoneType
    number: str


class ContactBase(PmsBaseModel):
    id: int
    name: str
    email: str = ""
    phones: list[Phone] | None = None
    country: CountryId | None = None

    @classmethod
    def parse_common_fields(cls, partner) -> dict:
        record_dict = {
            "id": partner.id,
            "name": partner.name,
            "email": partner.email or "",
            "country": CountryId.from_res_country(partner.country_id)
            if partner.country_id
            else None,
        }
        if partner.phone or partner.mobile:
            record_dict["phones"] = []
            if partner.phone:
                record_dict["phones"].append(
                    {"type": PhoneType.phone, "number": partner.phone}
                )
            if partner.mobile:
                record_dict["phones"].append(
                    {"type": PhoneType.mobile, "number": partner.mobile}
                )
        return record_dict


class ContactSummary(ContactBase):
    type: ContactType

    @classmethod
    def from_res_partner(cls, partner):
        data = cls.parse_common_fields(partner)
        data["type"] = partner.pms_partner_type
        return cls(**data)


class ContactSearch:
    def __init__(
        self,
        globalSearch: str | None = Query(
            default=None,
            description="Search across name, email, phone and VAT fields"
            "this value (case-insensitive).",
        ),
        name: str | None = Query(
            default=None,
            description="Search for contacts whose name contains "
            "this value (case-insensitive).",
        ),
        email: str | None = Query(
            default=None,
            description="Search for contacts whose email contains this "
            "value (case-insensitive).",
        ),
        type: ContactType | None = Query(  # noqa: B008
            default=None, description="Filter contacts by type."
        ),
        country: str | None = Query(
            default=None,
            description="Search for contacts whose country contains this "
            "value (case-insensitive).",
        ),
    ):
        self.globalSearch = globalSearch
        self.name = name
        self.email = email
        self.contact_type = type
        self.country = country

    def to_odoo_domain(self, env: api.Environment) -> list:
        domain = []
        if self.globalSearch:
            domain += [
                "|",
                "|",
                "|",
                ("name", "ilike", self.globalSearch),
                ("email", "ilike", self.globalSearch),
                ("phone_mobile_search", "ilike", self.globalSearch),
                ("vat", "ilike", self.globalSearch),
            ]
        if self.name:
            domain.append(("name", "ilike", self.name))
        if self.email:
            domain.append(("email", "ilike", self.email))
        if self.contact_type:
            domain.append(("pms_partner_type", "=", self.contact_type.value))
        if self.country:
            domain.append(("country_id.name", "ilike", self.country))

        return domain
