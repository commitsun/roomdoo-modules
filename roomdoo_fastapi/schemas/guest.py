from datetime import date
from enum import Enum
from typing import Annotated

from fastapi import Query

from odoo import api
from odoo.osv import expression

from odoo.addons.pms_fastapi.schemas.base import BaseSearch
from odoo.addons.pms_fastapi.schemas.contact import ContactBase
from odoo.addons.pms_fastapi.schemas.pms_reservation import ReservationId
from odoo.addons.roomdoo_fastapi.schemas.id_document import IdDocument


class GuestOrderField(str, Enum):
    name = "name"
    country = "country"


GUEST_ORDER_MAPPING = {
    "name": "display_name",
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


class GuestSearch(BaseSearch):
    def __init__(
        self,
        pmsProperty: int | None = Query(
            default=None,
            description="Filter guests of the given property.",
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
        checkinDateFrom: Annotated[
            date | None,
            Query(
                description="Search contacts with a checkin between dates "
                "(only works if checkinDateTo is also setted)"
            ),
        ] = None,
        checkinDateTo: Annotated[
            date | None,
            Query(
                description="Search contacts with a checkin between dates "
                "(only works if checkinDateFrom is also setted)"
            ),
        ] = None,
    ):
        self.pmsProperty = pmsProperty
        self.globalSearch = globalSearch
        self.name = name
        self.email = email
        self.countries = countries
        self.inHouse = inHouse
        self.vat = vat
        self.phone = phone
        self.checkinDateFrom = checkinDateFrom
        self.checkinDateTo = checkinDateTo

    def to_odoo_domain(self, env: api.Environment) -> list:
        domain = []
        if self.pmsProperty:
            domain += [
                ("pms_checkin_partner_ids.pms_property_id", "=", self.pmsProperty)
            ]
        else:
            domain += [
                (
                    "pms_checkin_partner_ids.pms_property_id",
                    "in",
                    env.user.pms_property_ids.ids,
                )
            ]
        if self.globalSearch:
            domain += [
                "|",
                "|",
                "|",
                ("display_name", "ilike", self.globalSearch),
                ("email", "ilike", self.globalSearch),
                ("vat", "ilike", self.globalSearch),
                ("identification_number", "ilike", self.globalSearch),
            ]
            if len(self.globalSearch) >= 3:
                phone_domain = [("phone_mobile_search", "ilike", self.globalSearch)]
                domain = expression.OR([domain, phone_domain])
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
            domain.append(("display_name", "ilike", self.name))
        if self.phone:
            domain.append(("phone_mobile_search", "ilike", self.phone))
        if self.email:
            domain.append(("email", "ilike", self.email))
        if self.countries:
            subdomains = [[("country_id.name", "ilike", c)] for c in self.countries]
            domain = expression.AND([domain, expression.OR(subdomains)])
        if self.checkinDateFrom and self.checkinDateTo:
            subdomains = expression.OR(
                [
                    [
                        (
                            "pms_checkin_partner_ids.arrival",
                            ">=",
                            self.checkinDateFrom,
                        ),
                        ("pms_checkin_partner_ids.arrival", "<=", self.checkinDateTo),
                    ],
                    [
                        (
                            "pms_checkin_partner_ids.departure",
                            ">=",
                            self.checkinDateFrom,
                        ),
                        (
                            "pms_checkin_partner_ids.departure",
                            "<=",
                            self.checkinDateTo,
                        ),
                    ],
                ]
            )
            domain = expression.AND([domain, subdomains])
        return domain

    def to_odoo_context(self, env: api.Environment) -> dict:
        if self.pmsProperty:
            return {"pms_property_ids": [self.pmsProperty]}
        else:
            return {"pms_property_ids": env.user.pms_property_ids.ids}
