from enum import Enum
from typing import Annotated

from fastapi import Query

from odoo import api
from odoo.osv import expression

from odoo.addons.pms_fastapi.schemas.contact import ContactBase
from odoo.addons.pms_fastapi.schemas.pms_reservation import ReservationId
from odoo.addons.roomdoo_fastapi.schemas.id_document import IdDocument


class GuestOrderField(str, Enum):
    name = "name"
    country = "country"


GUEST_ORDER_MAPPING = {
    "name": "name",
    "country": "country_id",
}


class GuestSummary(ContactBase):
    identificationDocuments: list[IdDocument]
    internalNotes: str = ""
    lastReservation: ReservationId
    inHouse: bool

    @classmethod
    def from_res_partner(cls, partner):
        data = cls.parse_common_fields(partner)
        data["internalNotes"] = partner.comment or ""
        data["identificationDocuments"] = [
            IdDocument.from_id_number(x) for x in partner.id_numbers
        ]
        data["inHouse"] = partner.in_house
        if partner.last_reservation_id:
            data["lastReservation"] = ReservationId.from_pms_reservation(
                partner.last_reservation_id
            )
        return cls(**data)


class GuestSearch:
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
        inHouse: bool | None = Query(
            default=False,
            description="Search for contacts in house. Boolean value ",
        ),
        name: str | None = Query(
            default=None,
            description="Search for contacts whose name contains "
            "this value (case-insensitive).",
        ),
        phone: str | None = Query(
            default=None,
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
        self.inHouse = inHouse
        self.vat = vat
        self.phone = phone

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
            id_numbers = (
                env["res.partner.id_number"]
                .sudo()
                .search([("name", "ilike", self.vat)])
            )
            domain.append(("id", "in", id_numbers.mapped("partner_id.id")))
        if self.inHouse:
            domain.append(("in_house", "=", True))
        if self.name:
            domain.append(("name", "ilike", self.name))
        if self.phone:
            domain.append(("phone_mobile_search", "ilike", self.phone))
        if self.email:
            domain.append(("email", "ilike", self.email))
        if self.countries:
            subdomains = [[("country_id.name", "ilike", c)] for c in self.countries]
            domain = expression.AND([domain, expression.OR(subdomains)])
        return domain
