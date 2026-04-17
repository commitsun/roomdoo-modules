from datetime import date, datetime
from enum import Enum
from typing import Annotated

from fastapi import Query
from fastapi.params import Query as QueryType
from pydantic import Field, field_validator

from odoo import api
from odoo.osv import expression

from .base import BaseSearch, CurrencyAmount, PmsBaseModel
from .contact import ContactIdImage
from .country import CountrySummary
from .currency import CurrencySummary
from .pms_room import RoomId
from .pms_sale_channel import SaleChannelDetail
from .pms_service import ServiceId
from .reservation_guest import CheckinStateEnum


class ReportFormatEnum(str, Enum):
    pdf = "pdf"
    xlsx = "xlsx"


class FolioOrderField(str, Enum):
    CREATION_DATE = "creationDate"
    CHECKIN = "checkin"
    CHECKOUT = "checkout"
    STATE = "state"


FOLIO_ORDER_MAPPING = {
    "creationDate": "create_date",
    "checkin": "first_checkin",
    "checkout": "last_checkout",
    "state": "fastapi_sort_state",
}


class reservationStateEnum(str, Enum):
    DRAFT = "draft"
    ARRIVAL = "arrival"
    IN_HOUSE = "inHouse"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    OVERBOOKING = "overbooking"


class folioPaymentStateEnum(str, Enum):
    PAID = "paid"
    NOT_PAID = "notPaid"
    PARTIALLY_PAID = "partiallyPaid"
    OVERDUE = "overdue"
    OVERPAID = "overpaid"


# Reused from reservation_guest module
preCheckinStateEnum = CheckinStateEnum


class invoiceStateEnum(str, Enum):
    TO_INVOICE = "toInvoice"
    INVOICED = "invoiced"


class reservationSummary(PmsBaseModel):
    id: int
    name: str
    splitted: bool = Field(False, alias="isSplitted")
    partner_internal_comment: str = Field("", alias="notes")
    checkin_datetime: datetime | None = Field(None, alias="checkinDate")
    checkout_datetime: datetime | None = Field(None, alias="checkoutDate")
    adults: int = Field(0)
    children: int = Field(0)
    nights: int = Field(0)
    to_assign: bool = Field(False, alias="toAssign")
    rooms: list[RoomId]
    services: list[ServiceId]
    saleChannel: SaleChannelDetail | None = None
    agency: ContactIdImage | None = None
    state: reservationStateEnum
    price_room_services_set: CurrencyAmount = Field(0.0, alias="totalAmount")
    currency: CurrencySummary | None = None
    incompleteCheckinsCount: int = Field(0)
    completeCheckinsCount: int = Field(0)

    @classmethod
    def from_pms_reservation(cls, reservation):
        filtered_data = cls._read_odoo_record(reservation)
        filtered_data["rooms"] = [
            RoomId.from_pms_room(room)
            for room in reservation.reservation_line_ids.mapped("room_id")
        ]
        filtered_data["services"] = [
            ServiceId.from_pms_service(service) for service in reservation.service_ids
        ]
        if reservation.currency_id:
            filtered_data["_decimal_places"] = reservation.currency_id.decimal_places
            filtered_data["currency"] = CurrencySummary.from_res_currency(
                reservation.currency_id
            )
        if reservation.sale_channel_origin_id:
            filtered_data["saleChannel"] = SaleChannelDetail.from_pms_sale_channel(
                reservation.sale_channel_origin_id
            )
        if reservation.agency_id:
            filtered_data["agency"] = ContactIdImage.from_res_partner(
                reservation.agency_id
            )
        if reservation.overbooking and reservation.state != "cancel":
            filtered_data["state"] = reservationStateEnum.OVERBOOKING
        elif reservation.state == "draft":
            filtered_data["state"] = reservationStateEnum.DRAFT
        elif reservation.state == "cancel":
            filtered_data["state"] = reservationStateEnum.CANCELLED
        elif reservation.state in ("confirm", "arrival_delayed"):
            filtered_data["state"] = reservationStateEnum.ARRIVAL
        elif reservation.state in ("onboard", "departure_delayed"):
            filtered_data["state"] = reservationStateEnum.IN_HOUSE
        elif reservation.state == "done":
            filtered_data["state"] = reservationStateEnum.COMPLETED

        checkin_partners = reservation.checkin_partner_ids
        filtered_data["incompleteCheckinsCount"] = len(
            checkin_partners.filtered(lambda c: c.state in ("dummy", "draft"))
        )
        filtered_data["completeCheckinsCount"] = len(
            checkin_partners.filtered(
                lambda c: c.state in ("precheckin", "onboard", "done")
            )
        )
        return cls(**filtered_data)


class FolioSummary(PmsBaseModel):
    id: int
    partner_name: str = Field("", alias="customerName")
    name: str = Field(alias="name")
    external_reference: str = Field("", alias="externalReference")
    nationality: CountrySummary | None = None
    amount_total: CurrencyAmount = Field(0.0, alias="totalAmount")
    currency: CurrencySummary | None = None
    create_date: date = Field(alias="creationDate")
    reservations: list[reservationSummary]
    paymentState: folioPaymentStateEnum
    overdueDays: int = Field(0)
    inHouseGuestsCount: int = Field(0)
    pendingGuestsCount: int = Field(0)
    doneGuestsCount: int = Field(0)
    invoiceState: invoiceStateEnum
    payers: list[ContactIdImage] = Field(default_factory=list)

    @field_validator("create_date", mode="before")
    @classmethod
    def convert_datetime_to_date(cls, v):
        if isinstance(v, datetime):
            return v.date()
        return v

    @classmethod
    def from_pms_folio(cls, folio):
        filtered_data = cls._read_odoo_record(folio)
        filtered_data["reservations"] = [
            reservationSummary.from_pms_reservation(res)
            for res in folio.reservation_ids
            if res.cancelled_reason != "modified"
        ]
        if folio.partner_id.nationality_id:
            filtered_data["nationality"] = CountrySummary.from_res_country(
                folio.partner_id.nationality_id
            )
        if folio.currency_id:
            filtered_data["_decimal_places"] = folio.currency_id.decimal_places
            filtered_data["currency"] = CurrencySummary.from_res_currency(
                folio.currency_id
            )

        overdue_moves = [move for move in folio.move_ids if move.has_overdue_payments]
        if overdue_moves:
            filtered_data["paymentState"] = folioPaymentStateEnum.OVERDUE
            min_date = min(move.min_overdue_date for move in overdue_moves)
            filtered_data["overdueDays"] = (date.today() - min_date).days
        elif folio.payment_state in ("paid", "nothing_to_pay"):
            filtered_data["paymentState"] = folioPaymentStateEnum.PAID
        elif folio.payment_state == "not_paid":
            filtered_data["paymentState"] = folioPaymentStateEnum.NOT_PAID
        elif folio.payment_state == "partial":
            filtered_data["paymentState"] = folioPaymentStateEnum.PARTIALLY_PAID
        elif folio.payment_state == "overpayment":
            filtered_data["paymentState"] = folioPaymentStateEnum.OVERPAID

        # Guest counts from checkin_partner_ids across all reservations
        all_checkin_partners = folio.reservation_ids.filtered(
            lambda r: r.cancelled_reason != "modified"
        ).mapped("checkin_partner_ids")
        filtered_data["pendingGuestsCount"] = len(
            all_checkin_partners.filtered(
                lambda c: c.state in ("dummy", "draft", "precheckin")
            )
        )
        filtered_data["inHouseGuestsCount"] = len(
            all_checkin_partners.filtered(lambda c: c.state == "onboard")
        )
        filtered_data["doneGuestsCount"] = len(
            all_checkin_partners.filtered(lambda c: c.state == "done")
        )

        # Invoice state
        if folio.invoice_status in ("to_invoice", "to_confirm"):
            filtered_data["invoiceState"] = invoiceStateEnum.TO_INVOICE
        else:
            filtered_data["invoiceState"] = invoiceStateEnum.INVOICED

        # Payers from sale_line_ids default_invoice_to
        payer_partners = folio.sale_line_ids.mapped("default_invoice_to").filtered(
            lambda p: p.id
        )
        filtered_data["payers"] = [
            ContactIdImage.from_res_partner(partner) for partner in payer_partners
        ]

        return cls(**filtered_data)


class FolioSearch(BaseSearch):
    def __init__(
        self,
        pmsPropertyId: Annotated[
            int | None,
            Query(
                description="Filter folios of the given property.",
            ),
        ] = None,
        globalSearch: Annotated[
            str | None,
            Query(
                description="Search across folio name, external reference, "
                "and customer name.",
            ),
        ] = None,
        name: Annotated[
            str | None,
            Query(
                description="Search for folios whose name contains "
                "this value (case-insensitive).",
            ),
        ] = None,
        creationDate: Annotated[
            date | None,
            Query(
                description="Search for folios whose creation date is " "this value.",
            ),
        ] = None,
        paymentState: Annotated[
            folioPaymentStateEnum | None,
            Query(
                description="Search for folios whose payment state is " "this value.",
            ),
        ] = None,
        room: Annotated[
            str | None,
            Query(
                description="Search for folios whose room is " "this value.",
            ),
        ] = None,
        nights: Annotated[
            int | None,
            Query(
                description="Search for folios whose nights is " "this value.",
            ),
        ] = None,
        checkin: Annotated[
            date | None,
            Query(
                description="Search for folios whose checkin date is " "this value.",
            ),
        ] = None,
        checkout: Annotated[
            date | None,
            Query(
                description="Search for folios whose checkout date is " "this value.",
            ),
        ] = None,
        saleChannel: Annotated[
            str | None,
            Query(
                description="Search for folios whose sale channel is " "this value.",
            ),
        ] = None,
        agency: Annotated[
            str | None,
            Query(
                description="Search for folios whose agency is " "this value.",
            ),
        ] = None,
        reservationState: Annotated[
            reservationStateEnum | None,
            Query(
                description="Search for folios whose reservation state is "
                "this value.",
            ),
        ] = None,
        stayPeriodStart: Annotated[
            date | None,
            Query(
                description="Search for folios whose stay period starts on "
                "this value.",
            ),
        ] = None,
        stayPeriodEnd: Annotated[
            date | None,
            Query(
                description="Search for folios whose stay period ends on "
                "this value.",
            ),
        ] = None,
        origin: Annotated[
            str | None,
            Query(
                description="Combined search of channel and agency.",
            ),
        ] = None,
        totalAmountGt: Annotated[
            float | None,
            Query(
                description="Filter folios whose total amount is greater than "
                "this value.",
            ),
        ] = None,
        totalAmountLt: Annotated[
            float | None,
            Query(
                description="Filter folios whose total amount is less than "
                "this value.",
            ),
        ] = None,
        totalAmountEq: Annotated[
            float | None,
            Query(
                description="Filter folios whose total amount is equal to "
                "this value.",
            ),
        ] = None,
        checkinFrom: Annotated[
            date | None,
            Query(
                description="Checkin period range start. "
                "Only works if checkinTo is also set.",
            ),
        ] = None,
        checkinTo: Annotated[
            date | None,
            Query(
                description="Checkin period range end. "
                "Only works if checkinFrom is also set.",
            ),
        ] = None,
        checkoutFrom: Annotated[
            date | None,
            Query(
                description="Checkout period range start. "
                "Only works if checkoutTo is also set.",
            ),
        ] = None,
        checkoutTo: Annotated[
            date | None,
            Query(
                description="Checkout period range end. "
                "Only works if checkoutFrom is also set.",
            ),
        ] = None,
        preCheckinState: Annotated[
            preCheckinStateEnum | None,
            Query(
                description="Filter folios with at least one guest "
                "in this pre-checkin state.",
            ),
        ] = None,
        services: Annotated[
            list[int] | None,
            Query(
                description="Filter by service IDs. "
                "Repeated query param, e.g. ?services=1&services=5",
            ),
        ] = None,
        invoiceState: Annotated[
            invoiceStateEnum | None,
            Query(
                description="Filter by invoice state.",
            ),
        ] = None,
    ):
        if not isinstance(pmsPropertyId, QueryType):
            self.pmsProperty = pmsPropertyId
        else:
            self.pmsProperty = None
        self.globalSearch = globalSearch
        self.name = name
        self.creationDate = creationDate
        self.paymentState = paymentState
        self.room = room
        self.nights = nights
        self.checkin = checkin
        self.checkout = checkout
        self.saleChannel = saleChannel
        self.agency = agency
        self.reservationState = reservationState
        self.stayPeriodStart = stayPeriodStart
        self.stayPeriodEnd = stayPeriodEnd
        self.origin = origin
        self.totalAmountGt = totalAmountGt
        self.totalAmountLt = totalAmountLt
        self.totalAmountEq = totalAmountEq
        self.checkinFrom = checkinFrom
        self.checkinTo = checkinTo
        self.checkoutFrom = checkoutFrom
        self.checkoutTo = checkoutTo
        self.preCheckinState = preCheckinState
        self.services = services
        self.invoiceState = invoiceState

    def to_odoo_domain(self, env: api.Environment) -> list:
        domain = []
        simple_filters = [
            (self.name, "name", "ilike"),
            (self.creationDate, "create_date", ">="),
            (self.creationDate, "create_date", "<="),
        ]
        if self.pmsProperty:
            domain += [("pms_property_id", "=", self.pmsProperty)]
        else:
            domain += [
                (
                    "pms_property_id",
                    "in",
                    env.user.pms_property_ids.ids,
                )
            ]

        if self.globalSearch:
            domain = expression.AND(
                [
                    domain,
                    [
                        "|",
                        "|",
                        "|",
                        ("name", "ilike", self.globalSearch),
                        ("external_reference", "ilike", self.globalSearch),
                        ("partner_id", "child_of", self.globalSearch),
                        ("partner_name", "ilike", self.globalSearch),
                    ],
                ]
            )
        for value, field, operator in simple_filters:
            if value:
                domain.append((field, operator, value))

        if self.paymentState:
            domain.extend(self._get_payment_state_domain())

        if self.invoiceState:
            domain.extend(self._get_invoice_state_domain())

        reservation_folio_ids = self._get_reservation_folio_ids(env)
        if reservation_folio_ids is not None:
            domain.append(("id", "in", reservation_folio_ids))

        return domain

    def _get_reservation_folio_ids(self, env: api.Environment) -> list | None:
        """Search folios through reservation fields, excluding reservations
        cancelled due to modification (cancelled_reason = 'modified').
        Returns a list of folio IDs, or None if no reservation filters are active.
        """
        domain = self._build_reservation_domain()
        if not domain:
            return None
        groups = env["pms.reservation"].sudo().read_group(domain, [], ["folio_id"])
        return [g["folio_id"][0] for g in groups]

    def _build_reservation_domain(self) -> list:
        domain = []
        simple_filters = [
            (self.nights, "nights", "="),
            (self.checkin, "checkin", "="),
            (self.checkout, "checkout", "="),
            (self.room, "reservation_line_ids.room_id", "ilike"),
            (self.saleChannel, "sale_channel_origin_id", "ilike"),
            (self.agency, "agency_id", "ilike"),
        ]
        for value, field, operator in simple_filters:
            if value:
                domain.append((field, operator, value))
        if self.origin:
            domain = expression.AND(
                [
                    domain,
                    expression.OR(
                        [
                            [("sale_channel_origin_id", "ilike", self.origin)],
                            [("agency_id", "ilike", self.origin)],
                        ]
                    ),
                ]
            )
        if self.reservationState:
            domain.extend(self._get_reservation_state_domain())
        if self.stayPeriodStart and self.stayPeriodEnd:
            domain.extend(
                [
                    ("reservation_line_ids.date", ">=", self.stayPeriodStart),
                    ("reservation_line_ids.date", "<=", self.stayPeriodEnd),
                ]
            )
        if self.checkinFrom and self.checkinTo:
            domain.extend(
                [
                    ("checkin", ">=", self.checkinFrom),
                    ("checkin", "<=", self.checkinTo),
                ]
            )
        if self.checkoutFrom and self.checkoutTo:
            domain.extend(
                [
                    ("checkout", ">=", self.checkoutFrom),
                    ("checkout", "<=", self.checkoutTo),
                ]
            )
        if self.totalAmountGt is not None:
            domain.append(("price_room_services_set", ">", self.totalAmountGt))
        if self.totalAmountLt is not None:
            domain.append(("price_room_services_set", "<", self.totalAmountLt))
        if self.totalAmountEq is not None:
            domain.append(("price_room_services_set", "=", self.totalAmountEq))
        if self.preCheckinState:
            domain.extend(self._get_precheckin_state_domain())
        if self.services:
            domain.append(("service_ids", "in", self.services))
        if domain:
            domain.append(("cancelled_reason", "!=", "modified"))
        return domain

    def _get_reservation_state_domain(self) -> list:
        state_mapping = {
            reservationStateEnum.OVERBOOKING: [
                ("overbooking", "=", True),
                ("state", "!=", "cancel"),
            ],
            reservationStateEnum.CANCELLED: [("state", "=", "cancel")],
            reservationStateEnum.ARRIVAL: [
                ("state", "in", ["confirm", "arrival_delayed"])
            ],
            reservationStateEnum.IN_HOUSE: [
                ("state", "in", ["onboard", "departure_delayed"])
            ],
            reservationStateEnum.COMPLETED: [("state", "=", "done")],
            reservationStateEnum.DRAFT: [("state", "=", "draft")],
        }
        return state_mapping.get(self.reservationState, [])

    def _get_payment_state_domain(self, state=None) -> list:
        state = state if state is not None else self.paymentState
        state_mapping = {
            folioPaymentStateEnum.OVERDUE: [
                ("move_ids.has_overdue_payments", "=", True)
            ],
            folioPaymentStateEnum.PAID: [
                ("payment_state", "in", ["paid", "nothing_to_pay"])
            ],
            folioPaymentStateEnum.NOT_PAID: [("payment_state", "=", "not_paid")],
            folioPaymentStateEnum.PARTIALLY_PAID: [("payment_state", "=", "partial")],
            folioPaymentStateEnum.OVERPAID: [("payment_state", "=", "overpayment")],
        }
        return state_mapping.get(state, [])

    def _get_invoice_state_domain(self, state=None) -> list:
        state = state if state is not None else self.invoiceState
        state_mapping = {
            invoiceStateEnum.TO_INVOICE: [
                ("invoice_status", "in", ["to_invoice", "to_confirm"])
            ],
            invoiceStateEnum.INVOICED: [("invoice_status", "in", ["invoiced", "no"])],
        }
        return state_mapping.get(state, [])

    def _get_precheckin_state_domain(self) -> list:
        state_mapping = {
            CheckinStateEnum.pending: [("checkin_partner_ids.state", "=", "dummy")],
            CheckinStateEnum.partial: [("checkin_partner_ids.state", "=", "draft")],
            CheckinStateEnum.complete: [
                ("checkin_partner_ids.state", "in", ["precheckin", "onboard", "done"])
            ],
        }
        return state_mapping.get(self.preCheckinState, [])

    def to_odoo_context(self, env: api.Environment) -> dict:
        if self.pmsProperty:
            return {"pms_property_ids": [self.pmsProperty]}
        else:
            return {"pms_property_ids": env.user.pms_property_ids.ids}
