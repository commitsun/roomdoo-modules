# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import hashlib
import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Annotated, Any
from urllib.parse import parse_qsl, urlsplit

import pytz
from fastapi import Header, Request, Response
from fastapi.responses import RedirectResponse

from odoo import _, fields, models

from odoo.addons.pms_fastapi.dependencies import PublicEnv
from odoo.addons.pms_fastapi_booking_engine.models.fastapi_endpoint import (
    booking_engine_router,
)
from odoo.addons.pms_fastapi_booking_engine.schemas.availability import (
    CancellationType,
    Problem,
)
from odoo.addons.pms_fastapi_booking_engine.schemas.checkout import (
    Booking,
    BookingPayment,
    BookingPreview,
    BookingProperty,
    BookingSelection,
    BookingStatus,
    CreateBookingRequest,
    CreateBookingResponse,
    Holder,
    LastAttempt,
    NoAction,
    PaymentMethod,
    PaymentPlan,
    PriceSummary,
    RedirectAction,
    RetryPaymentRequest,
    RetryPaymentResponse,
    RoomLine,
)

from .availability import MAX_ROOMS, AvailabilitySearch

_logger = logging.getLogger(__name__)

HELPER = "pms_booking_engine.checkout_router.helper"
PROBLEM_RESPONSES = {
    400: {"model": Problem, "description": "Request not valid."},
    404: {"model": Problem, "description": "Unknown property or booking."},
    409: {"model": Problem, "description": "The selection cannot be booked."},
    "default": {"description": "Other errors."},
}
IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]
Authorization = Annotated[str | None, Header()]


@booking_engine_router.post(
    "/properties/{propertyId}/bookings/preview",
    response_model=BookingPreview,
    responses=PROBLEM_RESPONSES,
)
async def preview_booking(
    env: PublicEnv, propertyId: int, selection: BookingSelection
) -> BookingPreview:
    """Price a selection of offers again, with fresh data, and tell how it is
    paid. It creates nothing. Public: it needs no token."""
    return env[HELPER].new().preview_booking(propertyId, selection)


@booking_engine_router.post(
    "/properties/{propertyId}/bookings",
    status_code=201,
    response_model=CreateBookingResponse,
    responses=PROBLEM_RESPONSES,
)
async def create_booking(
    env: PublicEnv,
    request: Request,
    response: Response,
    propertyId: int,
    booking_request: CreateBookingRequest,
    idempotency_key: IdempotencyKey = None,
) -> CreateBookingResponse:
    """Book a selection. With something to pay now, the rooms are held until
    the payment arrives and nextAction takes the guest to the gateway. Send
    the same Idempotency-Key on the retries of one checkout."""
    result = (
        env[HELPER]
        .new()
        .create_booking(
            propertyId, booking_request, idempotency_key, _get_client_ip(request)
        )
    )
    if isinstance(result, CreateBookingResponse):
        response.headers["Location"] = f"/bookings/{result.booking.id}"
    return result


@booking_engine_router.get(
    "/bookings/{bookingId}", response_model=Booking, responses=PROBLEM_RESPONSES
)
async def get_booking(
    env: PublicEnv, bookingId: str, authorization: Authorization = None
) -> Booking:
    """Status and detail of a booking. Needs Authorization: Bearer with the
    accessToken of the creation."""
    return env[HELPER].new().get_booking(bookingId, authorization)


@booking_engine_router.post(
    "/bookings/{bookingId}/payments",
    status_code=201,
    response_model=RetryPaymentResponse,
    responses=PROBLEM_RESPONSES,
)
async def retry_payment(
    env: PublicEnv,
    bookingId: str,
    payment_request: RetryPaymentRequest,
    authorization: Authorization = None,
    idempotency_key: IdempotencyKey = None,
) -> RetryPaymentResponse:
    """Try the payment of a held booking again, with the same method or
    another one."""
    return (
        env[HELPER]
        .new()
        .retry_payment(bookingId, payment_request, authorization, idempotency_key)
    )


@booking_engine_router.api_route(
    "/bookings/{bookingId}/payment-return",
    methods=["GET", "POST"],
    include_in_schema=False,
)
async def payment_return(
    env: PublicEnv, request: Request, bookingId: str, result: str = "ok"
):
    """Where the gateway sends the guest back. It takes the result the
    gateway may bring along and redirects to the return URL of the booking."""
    data = {
        key: value for key, value in request.query_params.items() if key != "result"
    }
    if request.method == "POST":
        body = (await request.body()).decode("utf-8", errors="replace")
        data.update(parse_qsl(body))
    url = env[HELPER].new().payment_return(bookingId, result, data)
    return RedirectResponse(url, status_code=303)


def _get_client_ip(request):
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


@dataclass(eq=False)
class BookedUnit:
    """A unit of the selection, with the guests it lodges and its prices."""

    option: Any
    offer: Any
    adults: int = 0
    children: int = 0
    room_amount: float = 0.0
    tourist_tax: float = 0.0
    original: float | None = None
    discount: Any = None


@dataclass(eq=False)
class Quote:
    """A selection priced, and how it is paid."""

    search: Any
    units: list
    preview: Any
    currency: Any
    providers: dict = field(default_factory=dict)


class PmsBookingEngineCheckoutRouterHelper(models.AbstractModel):
    _name = HELPER
    _inherit = "pms_booking_engine.availability_router.helper"
    _description = "PMS Booking Engine Checkout Router Helper"

    # Endpoints

    def preview_booking(self, property_id, selection):
        helper = self.sudo()
        pms_property = helper._get_bookable_property(property_id)
        if not pms_property:
            return helper._property_not_found_problem(property_id)
        quote = helper.with_company(pms_property.company_id)._get_quote(
            pms_property, selection
        )
        return quote if not isinstance(quote, Quote) else quote.preview

    def create_booking(self, property_id, request, idempotency_key, client_ip):
        helper = self.sudo()
        pms_property = helper._get_bookable_property(property_id)
        if not pms_property:
            return helper._property_not_found_problem(property_id)
        return helper.with_company(pms_property.company_id)._create_booking(
            pms_property, request, idempotency_key, client_ip
        )

    def get_booking(self, public_id, authorization):
        helper = self.sudo()
        booking = helper._get_authorized_booking(public_id, authorization)
        if not booking:
            return helper._booking_not_found_problem()
        return helper.with_company(booking.folio_id.company_id)._to_booking(booking)

    def retry_payment(self, public_id, request, authorization, idempotency_key):
        helper = self.sudo()
        booking = helper._get_authorized_booking(public_id, authorization)
        if not booking:
            return helper._booking_not_found_problem()
        return helper.with_company(booking.folio_id.company_id)._retry_payment(
            booking, request, idempotency_key
        )

    def payment_return(self, public_id, result, data):
        helper = self.sudo()
        booking = helper.env["pms.booking.engine.booking"].search(
            [("public_id", "=", public_id)], limit=1
        )
        if not booking or not booking.return_url:
            return "/"
        transaction = booking.transaction_ids.sorted("id")[-1:]
        if transaction and data:
            helper._handle_returned_payment(transaction, data)
        return booking._get_guest_return_url("ok" if result == "ok" else "ko")

    # Quote

    def _get_quote(self, pms_property, selection):
        """Quote of the selection, or the problem that stops it."""
        rooms = sum(item.quantity for item in selection.rooms)
        if selection.extras:
            return self._problem(
                400,
                "extra-not-available",
                _("Extra not available"),
                _("The booking engine does not sell extras yet."),
                extraIds=[extra.extraId for extra in selection.extras],
            )
        if selection.adults < rooms <= MAX_ROOMS:
            return self._occupancy_not_covered_problem()
        today = self._get_property_today(pms_property)
        problem = self._check_search(
            today,
            selection.checkIn,
            selection.checkOut,
            selection.adults,
            selection.children,
            rooms,
        )
        if problem:
            return problem
        search = AvailabilitySearch(
            pms_property=pms_property,
            check_in=selection.checkIn,
            check_out=selection.checkOut,
            adults=selection.adults,
            children=selection.children,
            rooms=rooms,
            today=today,
        )
        units = self._get_selected_units(search, selection)
        if not isinstance(units, list):
            return units
        occupancies = self._distribute_guests(
            search, [unit.option.capacity for unit in units]
        )
        if occupancies is None:
            return self._occupancy_not_covered_problem()
        for unit, (adults, children) in zip(units, occupancies, strict=True):
            self._price_unit(search, unit, adults, children)
        currency = units[0].offer.pricelist.currency_id
        quote = Quote(search=search, units=units, preview=None, currency=currency)
        quote.preview = self._to_preview(quote)
        return quote

    def _get_selected_units(self, search, selection):
        """One BookedUnit per unit selected, or the problem when an offer does
        not exist or cannot sell that many units."""
        wanted = {}
        for item in selection.rooms:
            parsed = self._parse_offer_id(item.offerId)
            wanted[item.offerId] = parsed
        valid = [parsed for parsed in wanted.values() if parsed]
        room_types = self._get_room_types(search.pms_property).filtered(
            lambda room_type: room_type.id in {parsed[1] for parsed in valid}
        )
        pricelists = self._get_published_pricelists(search.pms_property)
        options = self._get_room_type_options(search, pricelists, room_types)
        offers = {
            offer.id: (option, offer) for option in options for offer in option.offers
        }
        missing = [offer_id for offer_id in wanted if offer_id not in offers]
        if missing:
            return self._offer_unavailable_problem(missing)
        units = [
            BookedUnit(option=offers[item.offerId][0], offer=offers[item.offerId][1])
            for item in selection.rooms
            for _unit in range(item.quantity)
        ]
        exceeded = self._get_exceeded_offers(search, units)
        if exceeded:
            return self._offer_unavailable_problem(exceeded)
        return units

    def _parse_offer_id(self, offer_id):
        """(pricelist id, room type id, board id) of an offer id, or None."""
        parts = offer_id.split("-")
        if len(parts) != 3 or not all(part.isdigit() for part in parts):
            return None
        return tuple(int(part) for part in parts)

    def _get_exceeded_offers(self, search, units):
        """Offers selected beyond what the availability can sell, with the
        same limits as the recommended rooms."""
        by_offer = Counter(unit.offer.id for unit in units)
        by_plan = Counter(unit.offer.plan_key for unit in units)
        by_room_type = Counter(unit.option.room_type.id for unit in units)
        exceeded = []
        for unit in units:
            offer, option = unit.offer, unit.option
            if offer.id in exceeded:
                continue
            if (
                by_offer[offer.id] > offer.max_quantity
                or by_room_type[option.room_type.id] > option.available_units
                or (
                    offer.max_avail_limit is not None
                    and by_plan[offer.plan_key] > offer.max_avail_limit
                )
                or (
                    offer.quota_limit is not None
                    and by_room_type[option.room_type.id] > offer.quota_limit
                )
            ):
                exceeded.append(offer.id)
        if not exceeded and not self._can_assign_rooms(
            search, [unit.option for unit in units]
        ):
            exceeded = list(dict.fromkeys(unit.offer.id for unit in units))
        return exceeded

    def _price_unit(self, search, unit, adults, children):
        """Price of the unit with its guests, split into room and tourist tax,
        and the original price when it is cheaper than the reference rate."""
        unit.adults, unit.children = adults, children
        amounts = {
            offer.id: self._get_unit_amount(search, offer, adults, children)
            for offer in unit.option.offers
        }
        taxes = {
            offer.id: self._get_unit_tourist_tax(search, offer, adults, children)
            for offer in unit.option.offers
        }
        offer = unit.offer
        currency = offer.pricelist.currency_id
        # The room lines go without tourist tax, and so do their discounts
        room_amounts = {
            offer_id: currency.round(amount - taxes[offer_id])
            for offer_id, amount in amounts.items()
            if amount is not None
        }
        unit.tourist_tax = taxes[offer.id]
        unit.room_amount = room_amounts[offer.id]
        references = self._get_reference_offers(
            [other for other in unit.option.offers if other.id in room_amounts],
            room_amounts,
        )
        reference = references[self._get_reference_key(offer)]
        unit.original, unit.discount = self._get_discount(
            offer, unit.room_amount, reference, room_amounts[reference.id]
        )

    def _get_unit_tourist_tax(self, search, offer, adults, children):
        """Tourist tax of one unit, as the reservation will bill it."""
        return offer.pricelist.currency_id.round(
            sum(
                self._get_billed_amount(search, offer.pricelist, product, lines)
                for product, lines in self._get_tourist_tax_lines(
                    search, offer.pricelist, offer.room_type, adults, children
                ).items()
            )
        )

    def _to_preview(self, quote):
        search, currency = quote.search, quote.currency
        rooms = [self._to_room_line(search, unit) for unit in quote.units]
        rooms_total = currency.round(sum(unit.room_amount for unit in quote.units))
        tourist_tax = currency.round(sum(unit.tourist_tax for unit in quote.units))
        discount = currency.round(
            sum(
                unit.original - unit.room_amount
                for unit in quote.units
                if unit.original is not None
            )
        )
        total = currency.round(rooms_total + tourist_tax)
        due_now = self._get_due_now(quote)
        if currency.compare_amounts(due_now, 0) > 0:
            quote.providers = self._get_payment_providers(
                search.pms_property, due_now, currency
            )
            if not quote.providers:
                # Nothing to pay online with: everything is paid at the property
                due_now = 0.0
        pricelists = []
        for unit in quote.units:
            if unit.offer.pricelist not in pricelists:
                pricelists.append(unit.offer.pricelist)
        price_kwargs = {"_decimal_places": currency.decimal_places}
        return BookingPreview(
            stay=self._to_stay(search),
            rooms=rooms,
            extras=[],
            ratePlans=[
                self._to_rate_plan(search, pricelist) for pricelist in pricelists
            ],
            price=PriceSummary(
                currency=currency.name,
                rooms=rooms_total,
                extras=0.0,
                touristTax=tourist_tax,
                discount=discount,
                total=total,
                **price_kwargs,
            ),
            payment=PaymentPlan(
                dueNow=due_now,
                later=[],
                atProperty=currency.round(total - due_now),
                **price_kwargs,
            ),
            paymentMethods=(
                [method for method in PaymentMethod if method.value in quote.providers]
                if currency.compare_amounts(due_now, 0) > 0
                else []
            ),
        )

    def _to_room_line(self, search, unit):
        offer = unit.offer
        return RoomLine(
            roomTypeId=str(offer.room_type.id),
            roomTypeName=self._get_room_type_name(offer.room_type),
            offerId=offer.id,
            ratePlanId=str(offer.pricelist.id),
            boardCode=self._get_board_code(offer.board),
            includedServices=self._get_included_services(
                offer, unit.adults, unit.children
            ),
            adults=unit.adults,
            children=unit.children,
            price=self._to_price(
                search,
                offer.pricelist.currency_id,
                unit.room_amount,
                unit.original,
                unit.discount,
            ),
        )

    # Payment plan

    def _get_due_now(self, quote):
        """What the gateway charges on creation.

        Provisional rule until the payment policies of the rates exist: the
        rooms of a rate that cannot be cancelled for free from today are paid
        in full now, their tourist tax included; the rest at the property.
        """
        total = 0.0
        for unit in quote.units:
            cancellation = self._get_cancellation(quote.search, unit.offer.pricelist)
            if cancellation.type == CancellationType.nonRefundable:
                total += unit.room_amount + unit.tourist_tax
        return quote.currency.round(total)

    def _get_payment_providers(self, pms_property, amount, currency):
        """Provider of each payment method the property can charge the amount
        with, the first one by sequence."""
        providers = self.env["payment.provider"]._get_compatible_providers(
            pms_property.company_id.id,
            self.env.ref("pms.various_pms_partner").id,
            amount,
            currency_id=currency.id,
            pms_property_id=pms_property.id,
        )
        methods = {}
        for provider in providers.sorted(
            lambda provider: (provider.sequence, provider.id)
        ):
            method = provider._get_booking_engine_payment_method()
            if method and method not in methods:
                methods[method] = provider
        return methods

    # Creation

    def _create_booking(self, pms_property, request, idempotency_key, client_ip):
        if not idempotency_key or len(idempotency_key) > 255:
            return self._invalid_parameter_problem("Idempotency-Key")
        request_hash = hashlib.sha256(
            json.dumps(request.model_dump(mode="json"), sort_keys=True).encode()
        ).hexdigest()
        Booking = self.env["pms.booking.engine.booking"]
        existing = Booking.search(
            [
                ("pms_property_id", "=", pms_property.id),
                ("idempotency_key", "=", idempotency_key),
            ],
            limit=1,
        )
        if existing:
            if existing.request_hash != request_hash:
                return self._problem(
                    400,
                    "idempotency-key-reused",
                    _("Idempotency key reused"),
                    _("The Idempotency-Key was already used for another request."),
                )
            return self._to_create_response(existing, self._get_replay_action(existing))
        if not request.termsAccepted:
            return self._problem(
                400,
                "terms-not-accepted",
                _("Terms not accepted"),
                _("The guest has to accept the booking conditions."),
            )
        if not self._is_return_url_allowed(pms_property, request.returnUrl):
            return self._return_url_problem()
        quote = self._get_quote(pms_property, request)
        if not isinstance(quote, Quote):
            return quote
        preview = quote.preview
        currency = quote.currency
        if currency.compare_amounts(
            request.expected.total, preview.price.total
        ) or currency.compare_amounts(request.expected.dueNow, preview.payment.dueNow):
            return self._problem(
                409,
                "price-changed",
                _("Price changed"),
                _("The price changed since the preview: show the new one."),
                preview=preview.model_dump(mode="json"),
            )
        method = request.paymentMethod.value if request.paymentMethod else None
        offered = [option.value for option in preview.paymentMethods]
        if (offered and method not in offered) or (not offered and method):
            return self._problem(
                400,
                "payment-method-not-available",
                _("Payment method not available"),
                _("The payment method is not one of the methods of the preview."),
            )
        folio = self._create_folio(quote, request)
        due_now = preview.payment.dueNow
        hold_minutes = pms_property.booking_engine_hold_minutes or 15
        booking = Booking.create(
            {
                "folio_id": folio.id,
                "idempotency_key": idempotency_key,
                "request_hash": request_hash,
                "return_url": request.returnUrl,
                "language": request.language,
                "holder_first_name": request.holder.firstName,
                "holder_last_name": request.holder.lastName,
                "holder_email": request.holder.email,
                "holder_phone": request.holder.phone,
                "holder_country": request.holder.country,
                "arrival_time": request.arrivalTime,
                "requests": request.requests,
                "marketing_opt_in": request.marketingOptIn,
                "terms_accepted_at": fields.Datetime.now(),
                "terms_ip": client_ip,
                "preview_data": json.dumps(preview.model_dump(mode="json")),
                "expires_at": (
                    fields.Datetime.now() + timedelta(minutes=hold_minutes)
                    if method
                    else False
                ),
            }
        )
        if not method:
            booking._confirm()
            return self._to_create_response(booking, {"type": "none"})
        transaction = booking._create_transaction(quote.providers[method], due_now)
        return self._to_create_response(
            booking, transaction._get_booking_engine_next_action()
        )

    def _create_folio(self, quote, request):
        """Folio of the selection, its reservations held as pre-reservations."""
        search = quote.search
        pms_property = search.pms_property
        channel = pms_property.booking_engine_sale_channel_id
        holder = request.holder
        contact = {
            "partner_name": f"{holder.firstName} {holder.lastName}".strip(),
            "email": holder.email,
            "mobile": holder.phone,
        }
        folio_vals = {
            "pms_property_id": pms_property.id,
            "sale_channel_origin_id": channel.id,
            **contact,
        }
        lang = self._get_odoo_lang(request.language)
        if lang:
            folio_vals["lang"] = lang
        folio = self.env["pms.folio"].create(folio_vals)
        Reservation = self.env["pms.reservation"]
        for unit in quote.units:
            offer = unit.offer
            vals = {
                "folio_id": folio.id,
                "pms_property_id": pms_property.id,
                "room_type_id": offer.room_type.id,
                "pricelist_id": offer.pricelist.id,
                "board_service_room_id": offer.board.id or False,
                "checkin": search.check_in,
                "checkout": search.check_out,
                "adults": unit.adults,
                "children": unit.children,
                "sale_channel_origin_id": channel.id,
                "partner_requests": request.requests or False,
                # Held as a pre-reservation until it is paid
                "preconfirm": False,
                **contact,
            }
            if request.arrivalTime:
                vals["arrival_hour"] = request.arrivalTime
            if "board_service_room_manual" in Reservation._fields:
                # Keep the board of the offer, room only included, instead of
                # the default board of the pricelist
                vals["board_service_room_manual"] = True
            Reservation.create(vals)
        self._check_folio_amount(folio, quote)
        return folio

    def _check_folio_amount(self, folio, quote):
        """Warn when the folio does not bill what the guest was shown. Before
        confirmation it has no tourist tax."""
        expected = quote.currency.round(sum(unit.room_amount for unit in quote.units))
        if quote.currency.compare_amounts(folio.amount_total, expected):
            _logger.warning(
                "Booking engine: folio %s bills %s but the guest was shown %s",
                folio.name,
                folio.amount_total,
                expected,
            )

    def _get_odoo_lang(self, language):
        """Installed language of a BCP 47 tag: the same one, or the first one
        of the same language."""
        code = (language or "").replace("-", "_")
        installed = [
            lang_code for lang_code, _name in self.env["res.lang"].get_installed()
        ]
        if code in installed:
            return code
        prefix = code.split("_")[0].lower()
        return next(
            (
                lang_code
                for lang_code in installed
                if lang_code.split("_")[0].lower() == prefix
            ),
            False,
        )

    def _is_return_url_allowed(self, pms_property, url):
        parts = urlsplit(url or "")
        if parts.scheme not in ("https", "http") or not parts.netloc:
            return False
        origin = f"{parts.scheme}://{parts.netloc}".lower()
        return origin in pms_property._get_booking_engine_allowed_origins()

    def _get_replay_action(self, booking):
        """Next action of a creation retried: the same payment while it can
        still be made."""
        transaction = booking.transaction_ids.sorted("id")[:1]
        if booking.state == "pending_payment" and transaction.state == "draft":
            return transaction._get_booking_engine_next_action()
        return {"type": "none"}

    # Payments

    def _retry_payment(self, booking, request, idempotency_key):
        if not idempotency_key or len(idempotency_key) > 255:
            return self._invalid_parameter_problem("Idempotency-Key")
        previous = booking.transaction_ids.filtered(
            lambda tx: tx.booking_engine_idempotency_key == idempotency_key
        )[:1]
        if previous:
            action = (
                previous._get_booking_engine_next_action()
                if previous.state == "draft"
                else {"type": "none"}
            )
            return RetryPaymentResponse(nextAction=self._to_next_action(action))
        if booking.state != "pending_payment" or (
            booking.expires_at and booking.expires_at < fields.Datetime.now()
        ):
            status = self._get_booking_status(booking)
            return self._problem(
                409,
                "booking-not-payable",
                _("Booking not payable"),
                _("Only a booking waiting for its payment can be paid."),
                # "status" is the HTTP status of the problem (RFC 9457)
                bookingStatus=status.value,
            )
        pms_property = booking.pms_property_id
        if not self._is_return_url_allowed(pms_property, request.returnUrl):
            return self._return_url_problem()
        plan = json.loads(booking.preview_data)["payment"]
        currency = booking.folio_id.currency_id
        providers = self._get_payment_providers(pms_property, plan["dueNow"], currency)
        provider = providers.get(request.paymentMethod.value)
        if not provider:
            return self._problem(
                400,
                "payment-method-not-available",
                _("Payment method not available"),
                _("The property does not accept this payment method."),
            )
        booking.return_url = request.returnUrl
        transaction = booking._create_transaction(
            provider, plan["dueNow"], idempotency_key
        )
        return RetryPaymentResponse(
            nextAction=self._to_next_action(
                transaction._get_booking_engine_next_action()
            )
        )

    def _handle_returned_payment(self, transaction, data):
        """Process the result the gateway sent along with the guest: some
        terminals never notify the server, and the payment would be lost."""
        try:
            with self.env.cr.savepoint():
                self.env["payment.transaction"]._handle_notification_data(
                    transaction.provider_code, data
                )
        except Exception:
            # The guest must get back to the engine whatever the gateway sent
            _logger.exception(
                "Booking engine: result of %s not processed on return",
                transaction.reference,
            )

    # Booking

    def _get_authorized_booking(self, public_id, authorization):
        token = None
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        return self.env["pms.booking.engine.booking"]._find_by_token(public_id, token)

    def _to_create_response(self, booking, next_action):
        return CreateBookingResponse(
            booking=self._to_booking(booking),
            accessToken=booking.access_token,
            nextAction=self._to_next_action(next_action),
        )

    def _to_next_action(self, action):
        if action["type"] == "redirect":
            return RedirectAction(**action)
        return NoAction()

    def _to_booking(self, booking):
        preview = BookingPreview.model_validate(json.loads(booking.preview_data))
        currency = booking.folio_id.currency_id
        status = self._get_booking_status(booking)
        tz = pytz.timezone(booking.pms_property_id.tz or "UTC")
        return Booking(
            id=booking.public_id,
            code=booking.folio_id.name,
            status=status,
            expiresAt=(
                self._localize(booking.expires_at, tz)
                if status == BookingStatus.pendingPayment and booking.expires_at
                else None
            ),
            createdAt=self._localize(booking.create_date, tz),
            language=booking.language or "",
            property=self._to_booking_property(booking.pms_property_id),
            stay=preview.stay,
            holder=Holder(
                firstName=booking.holder_first_name or "",
                lastName=booking.holder_last_name or "",
                email=booking.holder_email or "",
                phone=booking.holder_phone or "",
                country=booking.holder_country or None,
            ),
            arrivalTime=booking.arrival_time or None,
            requests=booking.requests or None,
            rooms=preview.rooms,
            extras=preview.extras,
            ratePlans=preview.ratePlans,
            price=preview.price,
            payment=self._to_booking_payment(booking, preview, currency),
            manageUrl=None,
        )

    def _get_booking_status(self, booking):
        if booking.state == "pending_payment":
            return BookingStatus.pendingPayment
        if booking.state == "expired":
            return BookingStatus.expired
        reservations = booking.folio_id.reservation_ids
        if reservations and all(
            reservation.state == "cancel" for reservation in reservations
        ):
            return BookingStatus.cancelled
        return BookingStatus.confirmed

    def _to_booking_payment(self, booking, preview, currency):
        transactions = booking.transaction_ids.sorted("id")
        paid = transactions.filtered(lambda tx: tx.state == "done")
        last = transactions[-1:]
        method_tx = paid[-1:] or last
        method = (
            method_tx.provider_id._get_booking_engine_payment_method()
            if method_tx
            else None
        )
        attempt = last._get_booking_engine_attempt_status() if last else None
        return BookingPayment(
            paid=sum(paid.mapped("amount")),
            later=preview.payment.later,
            atProperty=preview.payment.atProperty,
            method=method,
            card=None,
            lastAttempt=LastAttempt(status=attempt) if attempt else None,
            _decimal_places=currency.decimal_places,
        )

    def _to_booking_property(self, pms_property):
        address = ", ".join(
            part
            for part in (
                pms_property.street,
                " ".join(
                    part for part in (pms_property.zip, pms_property.city) if part
                ),
            )
            if part
        )
        return BookingProperty(
            id=str(pms_property.id),
            name=pms_property.name,
            address=address,
            phone=pms_property.phone or None,
            email=pms_property.email or None,
            photo=None,
        )

    def _localize(self, value, tz):
        return pytz.utc.localize(value).astimezone(tz) if value else None

    # Problems

    def _property_not_found_problem(self, property_id):
        return self._problem(
            404,
            "property-not-found",
            _("Property not found"),
            _("There is no property bookable online with id %s.") % property_id,
            propertyId=property_id,
        )

    def _booking_not_found_problem(self):
        return self._problem(
            404,
            "booking-not-found",
            _("Booking not found"),
            _("There is no booking with this id and token."),
        )

    def _occupancy_not_covered_problem(self):
        return self._problem(
            400,
            "occupancy-not-covered",
            _("Occupancy not covered"),
            _(
                "The rooms selected cannot lodge the guests, or there are more "
                "rooms than adults."
            ),
        )

    def _offer_unavailable_problem(self, offer_ids):
        return self._problem(
            409,
            "offer-unavailable",
            _("Offer unavailable"),
            _("Some offers selected cannot be booked any more."),
            offerIds=offer_ids,
        )

    def _return_url_problem(self):
        return self._problem(
            400,
            "return-url-not-allowed",
            _("Return URL not allowed"),
            _("The return URL is not on a website allowed for the property."),
        )
