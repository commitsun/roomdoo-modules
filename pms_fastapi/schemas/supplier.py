from enum import Enum
from typing import Annotated

from fastapi import Query
from fastapi.params import Query as QueryType
from pydantic import Field

from odoo import api
from odoo.osv import expression

from odoo.addons.pms_fastapi.schemas.base import BaseSearch
from odoo.addons.pms_fastapi.schemas.contact import ContactBase

from .base import CurrencyAmount


class SupplierOrderField(str, Enum):
    name = "name"
    country = "country"
    email = "email"


SUPPLIER_ORDER_MAPPING = {
    "name": "display_name",
    "country": "country_id",
    "email": "email",
}


class SupplierSummary(ContactBase):
    email: str = ""
    vat: str
    totalInvoiced: CurrencyAmount = Field(
        description="Total invoiced in the last 12 months"
    )

    @classmethod
    def from_res_partner(cls, partner):
        data = cls.parse_common_fields(partner)
        data["email"] = partner.email or ""
        data["vat"] = partner.vat or ""
        data["totalInvoiced"] = partner.with_context(
            invoice_type="in_invoice"
        ).fastapi_total_invoiced
        data["_decimal_places"] = partner.currency_id.decimal_places
        return cls(**data)


class SupplierSearch(BaseSearch):
    def __init__(
        self,
        pmsPropertyId: int | None = Query(
            default=None,
            description="Filter totalInvoiced by property.",
        ),
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
        if not isinstance(pmsPropertyId, QueryType):
            self.pmsPropertyId = pmsPropertyId
        else:
            self.pmsPropertyId = None
        self.globalSearch = globalSearch
        self.vat = vat
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
        if self.vat:
            domain.append(("vat", "ilike", self.vat))
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

    def to_odoo_context(self, env: api.Environment) -> dict:
        if self.pmsPropertyId:
            return {"pms_property_ids": [self.pmsPropertyId]}
        else:
            return {"pms_property_ids": env.user.pms_property_ids.ids}
