# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import datetime as dt
from enum import Enum
from typing import Annotated, Literal

from pydantic import Field

from odoo.addons.pms_fastapi.schemas.base import CurrencyAmount, PmsBaseModel

from .availability import IncludedService, Price, RatePlan, Stay


class SelectedOffer(PmsBaseModel):
    offerId: str = Field(description="Offer.id of the availability, as it came.")
    quantity: int = Field(ge=1)


class SelectedExtra(PmsBaseModel):
    extraId: str
    quantity: int = Field(ge=1)


class BookingSelection(PmsBaseModel):
    """What the guest chose: the body of the preview and the base of the
    creation."""

    checkIn: dt.date
    checkOut: dt.date = Field(
        description="Exclusive: the last night is the day before."
    )
    adults: int = Field(description="Adults of the whole booking.")
    children: int = Field(description="Children of the whole booking.")
    rooms: list[SelectedOffer] = Field(min_length=1)
    extras: list[SelectedExtra] = Field(
        default_factory=list, description="Not available yet: send an empty list."
    )


class RoomLine(PmsBaseModel):
    """One unit, with its guests."""

    roomTypeId: str
    roomTypeName: str
    offerId: str
    ratePlanId: str
    boardCode: str
    includedServices: list[IncludedService] = Field(default_factory=list)
    adults: int
    children: int
    price: Price = Field(description="Whole stay of this unit, without tourist tax.")


class ExtraPricing(str, Enum):
    perStay = "perStay"
    perNight = "perNight"
    perPerson = "perPerson"
    perPersonNight = "perPersonNight"


class ExtraLine(PmsBaseModel):
    extraId: str
    name: str
    pricing: ExtraPricing
    quantity: int
    unitPrice: CurrencyAmount
    total: CurrencyAmount


class PriceSummary(PmsBaseModel):
    currency: str = Field(description="ISO 4217 currency code")
    rooms: CurrencyAmount = Field(description="Sum of the room prices.")
    extras: CurrencyAmount
    touristTax: CurrencyAmount = Field(description="0 when it does not apply.")
    discount: CurrencyAmount = Field(
        description="Sum of original - total of the rooms; already taken off."
    )
    total: CurrencyAmount = Field(description="rooms + extras + touristTax")


class ScheduledCharge(PmsBaseModel):
    date: dt.date = Field(description="Day of the charge, at the property.")
    amount: CurrencyAmount


class PaymentPlan(PmsBaseModel):
    """dueNow + sum of later + atProperty = price.total"""

    dueNow: CurrencyAmount = Field(description="Charged by the gateway on creation.")
    later: list[ScheduledCharge] = Field(default_factory=list)
    atProperty: CurrencyAmount = Field(description="Paid at the property.")


class PaymentMethod(str, Enum):
    card = "card"
    bizum = "bizum"


class BookingPreview(PmsBaseModel):
    stay: Stay
    rooms: list[RoomLine]
    extras: list[ExtraLine] = Field(default_factory=list)
    ratePlans: list[RatePlan]
    price: PriceSummary
    payment: PaymentPlan
    paymentMethods: list[PaymentMethod] = Field(
        description="Methods this plan can be paid with; empty when nothing is "
        "charged online."
    )


class Holder(PmsBaseModel):
    firstName: str = Field(min_length=1)
    lastName: str = Field(min_length=1)
    email: str = Field(min_length=3)
    phone: str = Field(min_length=1, description='E.164, e.g. "+34600111222"')
    country: str | None = Field(None, description="ISO 3166-1 alpha-2")


class ExpectedAmounts(PmsBaseModel):
    total: float
    dueNow: float


class CreateBookingRequest(BookingSelection):
    holder: Holder
    arrivalTime: str | None = Field(
        None, pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$", description='"HH:MM"'
    )
    requests: str | None = None
    language: str = Field(description='BCP 47 ("es", "en"...): language of the mails')
    marketingOptIn: bool = False
    termsAccepted: bool
    paymentMethod: PaymentMethod | None = Field(
        None, description="Null only when the preview has no payment methods."
    )
    returnUrl: str = Field(
        description="Where the guest comes back from the gateway; one of the "
        "origins allowed for the property."
    )
    expected: ExpectedAmounts = Field(description="What the preview showed.")


class NoAction(PmsBaseModel):
    """Nothing to do: the booking is confirmed."""

    type: Literal["none"] = "none"


class RedirectAction(PmsBaseModel):
    """Build a form with the fields and submit it to the url."""

    type: Literal["redirect"] = "redirect"
    method: str
    url: str
    fields: dict[str, str]


NextAction = Annotated[NoAction | RedirectAction, Field(discriminator="type")]


class BookingStatus(str, Enum):
    pendingPayment = "pendingPayment"
    confirmed = "confirmed"
    expired = "expired"
    cancelled = "cancelled"


class BookingProperty(PmsBaseModel):
    id: str
    name: str
    address: str
    phone: str | None = None
    email: str | None = None
    photo: str | None = None


class AttemptStatus(str, Enum):
    pending = "pending"
    failed = "failed"


class LastAttempt(PmsBaseModel):
    status: AttemptStatus


class Card(PmsBaseModel):
    brand: str
    last4: str


class BookingPayment(PmsBaseModel):
    paid: CurrencyAmount
    later: list[ScheduledCharge] = Field(default_factory=list)
    atProperty: CurrencyAmount
    method: PaymentMethod | None = None
    card: Card | None = None
    lastAttempt: LastAttempt | None = None


class Booking(PmsBaseModel):
    id: str = Field(description="Opaque and not enumerable.")
    code: str = Field(description="Confirmation number the guest sees.")
    status: BookingStatus
    expiresAt: dt.datetime | None = Field(
        None, description="End of the hold; only while pendingPayment."
    )
    createdAt: dt.datetime
    language: str
    property: BookingProperty
    stay: Stay
    holder: Holder
    arrivalTime: str | None = None
    requests: str | None = None
    rooms: list[RoomLine]
    extras: list[ExtraLine] = Field(default_factory=list)
    ratePlans: list[RatePlan]
    price: PriceSummary
    payment: BookingPayment
    manageUrl: str | None = None


class CreateBookingResponse(PmsBaseModel):
    booking: Booking
    accessToken: str = Field(description="Credential of the booking; only sent here.")
    nextAction: NextAction


class RetryPaymentRequest(PmsBaseModel):
    paymentMethod: PaymentMethod
    returnUrl: str


class RetryPaymentResponse(PmsBaseModel):
    nextAction: NextAction
