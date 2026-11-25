from datetime import date
from enum import Enum

from pydantic import Field

from odoo.addons.pms_fastapi.schemas.base import CurrencyAmount, PmsBaseModel
from odoo.addons.pms_fastapi.schemas.currency import CurrencySummary


class PortalFolioState(str, Enum):
    confirmed = "confirmed"
    cancelled = "cancelled"
    done = "done"


class PortalPaymentState(str, Enum):
    notPaid = "notPaid"
    partiallyPaid = "partiallyPaid"
    paid = "paid"
    overpaid = "overpaid"
    nothingToPay = "nothingToPay"


class PortalGender(str, Enum):
    male = "male"
    female = "female"
    other = "other"


FOLIO_STATES = {
    "confirm": PortalFolioState.confirmed,
    "cancel": PortalFolioState.cancelled,
    "done": PortalFolioState.done,
}
PAYMENT_STATES = {
    "not_paid": PortalPaymentState.notPaid,
    "partial": PortalPaymentState.partiallyPaid,
    "paid": PortalPaymentState.paid,
    "overpayment": PortalPaymentState.overpaid,
    "nothing_to_pay": PortalPaymentState.nothingToPay,
}
# Guests whose check-in data is complete.
COMPLETED_GUEST_STATES = ("precheckin", "onboard", "done")


class PortalCustomer(PmsBaseModel):
    id: int | None
    firstName: str
    lastName: str
    lastName2: str | None
    gender: PortalGender | None
    email: str
    phone: str

    @classmethod
    def from_pms_folio(cls, folio):
        partner = folio.partner_id
        if not partner:
            return cls(
                id=None,
                firstName=folio.partner_name or "",
                lastName="",
                lastName2=None,
                gender=None,
                email=folio.email or "",
                phone=folio.mobile or "",
            )
        return cls(
            id=partner.id,
            firstName=partner.firstname or "",
            lastName=partner.lastname or "",
            lastName2=partner.lastname2 or None,
            gender=partner.gender or None,
            email=partner.email or "",
            phone=partner.mobile or partner.phone or "",
        )


class PortalSaleLine(PmsBaseModel):
    name: str
    amount: CurrencyAmount

    @classmethod
    def from_amount(cls, name, amount, currency):
        return cls(name=name, amount=amount, _decimal_places=currency.decimal_places)


class PortalFolio(PmsBaseModel):
    id: int
    code: str
    state: PortalFolioState
    reservationIds: list[int] = Field(default_factory=list)
    customer: PortalCustomer
    checkInDate: date
    checkOutDate: date
    arrivalHour: str | None
    departureHour: str | None
    adults: int
    children: int | None
    completedGuests: int
    currency: CurrencySummary
    totalAmount: CurrencyAmount
    pendingAmount: CurrencyAmount
    paymentState: PortalPaymentState
    saleLines: list[PortalSaleLine] = Field(default_factory=list)

    @classmethod
    def from_pms_folio(cls, folio, sale_lines):
        stays = folio.reservation_ids.filtered(lambda r: r.state != "cancel")
        first_stay = stays.sorted("checkin")[:1]
        last_stay = stays.sorted("checkout")[-1:]
        completed_guests = stays.checkin_partner_ids.filtered(
            lambda guest: guest.state in COMPLETED_GUEST_STATES
        )
        return cls(
            id=folio.id,
            code=folio.name,
            state=FOLIO_STATES[folio.state],
            reservationIds=folio.reservation_ids.ids,
            customer=PortalCustomer.from_pms_folio(folio),
            checkInDate=folio.first_checkin,
            checkOutDate=folio.last_checkout,
            arrivalHour=first_stay.arrival_hour or None,
            departureHour=last_stay.departure_hour or None,
            adults=sum(stays.mapped("adults")),
            children=sum(stays.mapped("children")),
            completedGuests=len(completed_guests),
            currency=CurrencySummary.from_res_currency(folio.currency_id),
            totalAmount=folio.amount_total,
            pendingAmount=folio.pending_amount,
            paymentState=PAYMENT_STATES[folio.payment_state],
            saleLines=sale_lines,
            _decimal_places=folio.currency_id.decimal_places,
        )
