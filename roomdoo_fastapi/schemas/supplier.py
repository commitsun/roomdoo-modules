from enum import Enum

from fastapi import Query
from pydantic import Field

from odoo import api
from odoo.tools.float_utils import float_round

from odoo.addons.pms_fastapi.schemas.contact import ContactBase


class SupplierOrderField(str, Enum):
    name = "name"
    country = "country"


SUPPLIER_ORDER_MAPPING = {
    "name": "name",
    "country": "country_id",
}


class SupplierSummary(ContactBase):
    vat: str
    totalInvoiced: float = Field(description="Total invoiced in the last 12 months")

    @classmethod
    def from_res_partner(cls, partner):
        precision = partner.currency_id.decimal_places
        data = cls.parse_common_fields(partner)
        data["vat"] = partner.vat or ""
        data["totalInvoiced"] = float_round(
            partner.with_context(invoice_type="in_invoice").total_invoiced_last_year,
            precision,
        )
        return cls(**data)


class SupplierSearch:
    def __init__(
        self,
        globalSearch: str | None = Query(
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
        self.globalSearch = globalSearch
        self.vat = vat
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
        if self.vat:
            domain.append(("vat", "ilike", self.vat))
        if self.name:
            domain.append(("name", "ilike", self.name))
        if self.email:
            domain.append(("email", "ilike", self.email))
        if self.country:
            domain.append(("country_id.name", "ilike", self.country))
        return domain
