# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from datetime import date, datetime
from enum import Enum

from pydantic import ConfigDict, Field

from odoo.addons.pms_fastapi.schemas.base import CurrencyAmount, PmsBaseModel


class Stay(PmsBaseModel):
    """The stay the availability was computed for."""

    checkIn: date
    checkOut: date = Field(description="Exclusive: the last night is the day before.")
    nights: int
    adults: int
    children: int
    rooms: int


class CancellationType(str, Enum):
    free = "free"
    nonRefundable = "nonRefundable"


class Cancellation(PmsBaseModel):
    type: CancellationType
    freeUntil: datetime | None = Field(
        None,
        description="Last moment to cancel for free, in the property time zone. "
        "Null when the rate is non refundable.",
    )


class RatePlan(PmsBaseModel):
    """A rate of the property; it holds the cancellation policy."""

    id: str
    name: str
    cancellation: Cancellation


class IncludedService(PmsBaseModel):
    code: str
    label: str


class Discount(PmsBaseModel):
    label: str
    percent: float


class Price(PmsBaseModel):
    total: CurrencyAmount = Field(
        description="Whole stay for one unit, taxes included.",
    )
    original: float | None = Field(
        None, description="Price before the discount; null when there is none."
    )
    perNight: CurrencyAmount = Field(
        description="total / nights, for display only: do not rebuild totals "
        "from it.",
    )
    currency: str = Field(description="ISO 4217 currency code")
    discount: Discount | None = None


class Offer(PmsBaseModel):
    """A row of the room type: a rate plan with a board."""

    id: str = Field(description="Opaque reference to send back when booking.")
    ratePlanId: str
    boardCode: str = Field(description="Board code; RO means room only.")
    includedServices: list[IncludedService] = Field(default_factory=list)
    price: Price
    maxQuantity: int = Field(description="Units of this offer one booking can take.")
    unitsLeft: int | None = Field(
        None, description="Scarcity warning; null means no warning."
    )


class RoomType(PmsBaseModel):
    id: str
    name: str
    photos: list[str] = Field(default_factory=list)
    beds: str
    capacity: int = Field(description="Guests per unit, without extra beds.")
    sizeM2: float | None = None
    amenities: list[str] = Field(default_factory=list)
    availableUnits: int = Field(
        description="Units free the whole stay, shared by all the offers of the "
        "room type."
    )
    offers: list[Offer] = Field(default_factory=list)


class RecommendedItem(PmsBaseModel):
    roomTypeId: str
    offerId: str
    quantity: int


class Recommended(PmsBaseModel):
    """The rooms that best fit the guests searched, with a single total."""

    coversPeople: int
    items: list[RecommendedItem] = Field(default_factory=list)
    price: Price


class AvailabilityResponse(PmsBaseModel):
    stay: Stay
    ratePlans: list[RatePlan] = Field(default_factory=list)
    roomTypes: list[RoomType] = Field(default_factory=list)
    recommended: Recommended | None = None


class Problem(PmsBaseModel):
    """RFC 9457 problem details. Some errors add members, such as field, min and
    max for the occupancy."""

    model_config = ConfigDict(extra="allow")

    type: str = Field(
        description="Relative URI of the error, e.g. /errors/stay-too-long"
    )
    title: str
    status: int
    detail: str
    field: str | None = Field(None, description="Request parameter at fault")
