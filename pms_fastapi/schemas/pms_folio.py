from datetime import date, datetime
from enum import Enum
from typing import Annotated

from fastapi import Query
from fastapi.params import Query as QueryType
from pydantic import Field, field_validator

from odoo import api

from .base import BaseSearch, PmsBaseModel
from .contact import ContactIdImage
from .country import CountryId
from .currency import CurrencySummary
from .pms_room import RoomId
from .pms_sale_channel import SaleChannelId
from .pms_service import ServiceId


class reservationStateEnum(str, Enum):
    ARRIVAL = "arrival"
    IN_HOUSE = "in_house"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    OVERBOOKING = "overbooking"


class folioPaymentStateEnum(str, Enum):
    PAID = "paid"
    NOT_PAID = "notPaid"
    PARTIALLY_PAID = "partiallyPaid"
    OVERDUE = "overdue"
    OVERPAID = "overpaid"


class reservationSummary(PmsBaseModel):
    id: int
    name: str
    splitted: bool = Field(False, alias="isSplitted")
    partner_internal_comment: str = Field("", alias="notes")
    checkin: date = Field(alias="checkinDate")
    checkout: date = Field(alias="checkoutDate")
    adults: int = Field(0)
    children: int = Field(0)
    nights: int = Field(0)
    to_assign: bool = Field(False, alias="toAssign")
    roomIds: list[RoomId]
    serviceIds: list[ServiceId]
    saleChannelId: SaleChannelId | None = None
    agencyId: ContactIdImage | None = None
    state: reservationStateEnum
    price_room_services_set: float = Field(0.0, alias="totalAmount")
    currency: CurrencySummary | None = None

    @classmethod
    def from_pms_reservation(cls, reservation):
        filtered_data = cls._read_odoo_record(reservation)
        filtered_data["roomIds"] = [
            RoomId.from_pms_room(room)
            for room in reservation.reservation_line_ids.mapped("room_id")
        ]
        filtered_data["serviceIds"] = [
            ServiceId.from_pms_service(service) for service in reservation.service_ids
        ]
        if reservation.currency_id:
            filtered_data["currency"] = CurrencySummary.from_res_currency(
                reservation.currency_id
            )
        if reservation.sale_channel_origin_id:
            filtered_data["saleChannelId"] = SaleChannelId.from_pms_sale_channel(
                reservation.sale_channel_origin_id
            )
        if reservation.agency_id:
            filtered_data["agencyId"] = ContactIdImage.from_res_partner(
                reservation.agency_id
            )
        if reservation.overbooking and reservation.state != "cancel":
            filtered_data["state"] = reservationStateEnum.OVERBOOKING
        elif reservation.state == "cancel":
            filtered_data["state"] = reservationStateEnum.CANCELLED
        elif reservation.state in ("confirm", "arrival_delayed"):
            filtered_data["state"] = reservationStateEnum.ARRIVAL
        elif reservation.state in ("onboard", "departure_delayed"):
            filtered_data["state"] = reservationStateEnum.IN_HOUSE
        elif reservation.state == "completed":
            filtered_data["state"] = reservationStateEnum.COMPLETED
        return cls(**filtered_data)


class FolioSummary(PmsBaseModel):
    id: int
    partner_name: str = Field("", alias="customerName")
    name: str = Field(alias="name")
    external_reference: str = Field("", alias="externalReference")
    nationality: CountryId | None = None
    amount_total: float = Field(0.0, alias="totalAmount")
    currency: CurrencySummary | None = None
    create_date: date = Field(alias="creationDate")
    reservations: list[reservationSummary]
    paymentState: folioPaymentStateEnum

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
        ]
        if folio.partner_id.nationality_id:
            filtered_data["nationality"] = CountryId.from_res_country(
                folio.partner_id.nationality_id
            )
        if folio.currency_id:
            filtered_data["currency"] = CurrencySummary.from_res_currency(
                folio.currency_id
            )

        if any(move.has_overdue_payments for move in folio.move_ids):
            filtered_data["paymentState"] = folioPaymentStateEnum.OVERDUE
        elif folio.payment_state in ("paid", "nothing_to_pay"):
            filtered_data["paymentState"] = folioPaymentStateEnum.PAID
        elif folio.payment_state == "not_paid":
            filtered_data["paymentState"] = folioPaymentStateEnum.NOT_PAID
        elif folio.payment_state == "partial":
            filtered_data["paymentState"] = folioPaymentStateEnum.PARTIALLY_PAID
        elif folio.payment_state == "overpayment":
            filtered_data["paymentState"] = folioPaymentStateEnum.OVERPAID
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
            str | None,
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
            int | None,
            Query(
                description="Search for folios whose sale channel is " "this value.",
            ),
        ] = None,
        agency: Annotated[
            int | None,
            Query(
                description="Search for folios whose agency is " "this value.",
            ),
        ] = None,
        reservationState: Annotated[
            str | None,
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
    ):
        if not isinstance(pmsPropertyId, QueryType):
            self.pmsPropertyId = pmsPropertyId
        else:
            self.pmsPropertyId = None
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

    def to_odoo_domain(self, env: api.Environment) -> list:
        domain = []
        simple_filters = [
            (self.pmsPropertyId, "property_id", "="),
            (self.name, "name", "ilike"),
            (self.creationDate, "create_date", "="),
            (self.room, "reservation_ids.reservation_line_ids.room_id", "ilike"),
            (self.nights, "reservation_ids.number_of_nights", "="),
            (self.checkin, "reservation_ids.checkin_date", "="),
            (self.checkout, "reservation_ids.checkout_date", "="),
            (self.saleChannel, "reservation_ids.sale_channel_origin_id", "ilike"),
            (self.agency, "reservation_ids.agency_id", "ilike"),
        ]

        for value, field, operator in simple_filters:
            if value:
                domain.append((field, operator, value))

        if self.reservationState:
            domain.extend(self._get_reservation_state_domain())

        if self.paymentState:
            domain.extend(self._get_payment_state_domain())

        # Período de estancia
        if self.stayPeriodStart and self.stayPeriodEnd:
            domain.extend(
                [
                    (
                        "reservation_ids.reservation_line_ids.date",
                        ">=",
                        self.stayPeriodStart,
                    ),
                    (
                        "reservation_ids.reservation_line_ids.date",
                        "<=",
                        self.stayPeriodEnd,
                    ),
                ]
            )

        return domain

    def _get_reservation_state_domain(self) -> list:
        state_mapping = {
            reservationStateEnum.OVERBOOKING: [
                ("reservation_ids.overbooking", "=", True),
                ("reservation_ids.state", "!=", "cancel"),
            ],
            reservationStateEnum.CANCELLED: [("reservation_ids.state", "=", "cancel")],
            reservationStateEnum.ARRIVAL: [
                ("reservation_ids.state", "in", ["confirm", "arrival_delayed"])
            ],
            reservationStateEnum.IN_HOUSE: [
                ("reservation_ids.state", "in", ["in_house", "departure_delayed"])
            ],
            reservationStateEnum.COMPLETED: [
                ("reservation_ids.state", "=", "completed")
            ],
        }
        return state_mapping.get(self.reservationState, [])

    def _get_payment_state_domain(self) -> list:
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
        return state_mapping.get(self.paymentState, [])

    def to_odoo_context(self, env: api.Environment) -> dict:
        if self.pmsPropertyId:
            return {"pms_property_ids": [self.pmsPropertyId]}
        else:
            return {"pms_property_ids": env.user.pms_property_ids.ids}
