from enum import Enum

from fastapi import Query
from pydantic import Field

from odoo import api
from odoo.tools.float_utils import float_round

from odoo.addons.pms_fastapi.schemas.contact import ContactBase


class CustomerOrderField(str, Enum):
    name = "name"
    country = "country"


CUSTOMER_ORDER_MAPPING = {
    "name": "name",
    "country": "country_id",
}


class CustomerSummary(ContactBase):
    vat: str
    totalInvoiced: float = Field(description="Total invoiced in the last 12 months")

    @classmethod
    def from_res_partner(cls, partner):
        precision = partner.currency_id.decimal_places
        data = cls.parse_common_fields(partner)
        data["vat"] = partner.vat or ""
        data["totalInvoiced"] = float_round(partner.total_invoiced_last_year, precision)
        return cls(**data)


class CustomerSearch:
    def __init__(
        self,
        global_search: str | None = Query(
            default=None,
            description="Search across name, email, phone and VAT fields"
            "this value (case-insensitive).",
        ),
        vat: str | None = Query(
            default=None,
            description="Search for contacts whose VAT contains this "
            "value (case-insensitive).",
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
        self.global_search = global_search
        self.vat = vat
        self.name = name
        self.email = email
        self.country = country

    def to_odoo_domain(self, env: api.Environment) -> list:
        domain = []
        if self.global_search:
            domain += [
                "|",
                "|",
                "|",
                ("name", "ilike", self.global_search),
                ("email", "ilike", self.global_search),
                ("phone_mobile_search", "ilike", self.global_search),
                ("vat", "ilike", self.global_search),
            ]
        if self.vat:
            domain.append(("vat", "ilike", self.vat))
        if self.name:
            domain.append(("name", "ilike", self.name))
        if self.email:
            domain.append(("email", "ilike", self.email))
        if self.country:
            domain.append(("country_id.name", "ilike", self.country))
        return domain
