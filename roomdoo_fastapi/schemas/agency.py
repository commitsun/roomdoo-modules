from enum import Enum
from typing import Annotated

from fastapi import Query

from odoo import api
from odoo.osv import expression

from odoo.addons.pms_fastapi.schemas.contact import ContactBase


class AgencyOrderField(str, Enum):
    name = "name"
    country = "country"
    email = "email"


AGENCY_ORDER_MAPPING = {
    "name": "display_name",
    "country": "country_id",
    "email": "email",
}


class AgencySummary(ContactBase):
    email: str = ""

    @classmethod
    def from_res_partner(cls, partner):
        data = cls.parse_common_fields(partner)
        data["email"] = partner.email or ""
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
        phone: str | None = Query(
            default=None,
            min_length=3,
            description="Search for contacts whose phones contains " "this value.",
        ),
        email: str | None = Query(
            default=None,
            description="Search for contacts whose email contains this "
            "value (case-insensitive).",
        ),
        countries: Annotated[
            list[str] | None,
            Query(
                description="Search for contacts whose countries is in the given "
                "list (case-insensitive). Use repeated query parameters, "
                "e.g., ?countries=Spain&countries=France",
            ),
        ] = None,
    ):
        self.globalSearch = globalSearch
        self.name = name
        self.email = email
        self.countries = countries
        self.phone = phone

    def to_odoo_domain(self, env: api.Environment) -> list:
        domain = []
        if self.globalSearch:
            domain += [
                "|",
                "|",
                ("display_name", "ilike", self.globalSearch),
                ("email", "ilike", self.globalSearch),
                ("vat", "ilike", self.globalSearch),
            ]
            if len(self.globalSearch) >= 3:
                phone_domain = [("phone_mobile_search", "ilike", self.globalSearch)]
                domain = expression.OR([domain, phone_domain])
        if self.name:
            domain.append(("display_name", "ilike", self.name))
        if self.phone:
            domain.append(("phone_mobile_search", "ilike", self.phone))
        if self.email:
            domain.append(("email", "ilike", self.email))
        if self.countries:
            subdomains = [[("country_id.name", "ilike", c)] for c in self.countries]
            domain = expression.AND([domain, expression.OR(subdomains)])
        return domain
