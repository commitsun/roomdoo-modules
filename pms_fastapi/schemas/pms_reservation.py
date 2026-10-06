from datetime import datetime
from enum import Enum

from pydantic import Field

from .agency import AgencyIdImage
from .base import CurrencyAmount, PmsBaseModel
from .currency import CurrencySummary
from .pms_room import RoomId
from .pms_sale_channel import SaleChannelDetail
from .pms_service import ServiceId


class ReservationId(PmsBaseModel):
    id: int
    name: str

    @classmethod
    def from_pms_reservation(cls, reservation):
        return ReservationId(id=reservation.id, name=reservation.name)


class reservationStateEnum(str, Enum):
    DRAFT = "draft"
    ARRIVAL = "arrival"
    IN_HOUSE = "inHouse"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    NO_SHOW = "noShow"
    OVERBOOKING = "overbooking"
    RESELLING = "reselling"


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
    agency: AgencyIdImage | None = None
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
            filtered_data["agency"] = AgencyIdImage.from_res_partner(
                reservation.agency_id
            )
        if reservation.overbooking and reservation.state != "cancel":
            filtered_data["state"] = reservationStateEnum.OVERBOOKING
        elif (
            reservation.is_reselling
            or any(reservation.reservation_line_ids.mapped("is_reselling"))
        ) and reservation.state != "cancel":
            filtered_data["state"] = reservationStateEnum.RESELLING
        elif reservation.state == "draft":
            filtered_data["state"] = reservationStateEnum.DRAFT
        elif reservation.state == "cancel":
            if reservation.cancelled_reason == "noshow":
                filtered_data["state"] = reservationStateEnum.NO_SHOW
            else:
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
