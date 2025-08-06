from enum import Enum

from fastapi import Query

from odoo import api

from odoo.addons.pms_fastapi.schemas.contact import ContactBase


class AgencyOrderField(str, Enum):
    name = "name"
    country = "country"


AGENCY_ORDER_MAPPING = {
    "name": "name",
    "country": "country_id",
}


class AgencySummary(ContactBase):
    @classmethod
    def from_res_partner(cls, partner):
        data = cls.parse_common_fields(partner)
        return cls(**data)


class AgencySearch:
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
        country: str | None = Query(
            default=None,
            description="Search for contacts whose country contains this "
            "value (case-insensitive).",
        ),
    ):
        self.globalSearch = globalSearch
        self.name = name
        self.email = email
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
        if self.country:
            domain.append(("country_id.name", "ilike", self.country))
        return domain
