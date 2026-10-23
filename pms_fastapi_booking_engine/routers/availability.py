# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import hashlib
import itertools
import logging
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from http import HTTPStatus
from typing import Annotated, Any

import pytz
from fastapi import Query
from fastapi.responses import JSONResponse

from odoo import _, models
from odoo.tools.float_utils import json_float_round

from odoo.addons.pms_fastapi.dependencies import PublicEnv
from odoo.addons.pms_fastapi_booking_engine.models.fastapi_endpoint import (
    booking_engine_router,
)
from odoo.addons.pms_fastapi_booking_engine.schemas.availability import (
    AvailabilityResponse,
    Cancellation,
    CancellationType,
    Discount,
    IncludedService,
    Offer,
    Price,
    Problem,
    RatePlan,
    Recommended,
    RecommendedItem,
    RoomType,
    Stay,
)

_logger = logging.getLogger(__name__)

# Same limits as the search panel of the engine. They also bound what a public
# request can cost.
MAX_ROOMS = 5
MAX_ADULTS = 10
MAX_CHILDREN = 6
MAX_NIGHTS = 30
MAX_DAYS_AHEAD = 730
# Combinations of room types the recommended rooms are searched among; beyond
# it, only the cheapest room types take part.
MAX_RECOMMENDATION_COMBINATIONS = 20000
# Pricelists based on other pricelists are followed up to this depth.
MAX_PRICELIST_CHAIN = 5
ROOM_ONLY_CODE = "RO"
# Offers with this many units left or fewer carry a scarcity warning.
SCARCITY_UNITS = 3


@booking_engine_router.get(
    "/properties/{propertyId}/availability",
    response_model=AvailabilityResponse,
    responses={
        400: {
            "model": Problem,
            "description": "Search not valid (application/problem+json).",
        },
        404: {
            "model": Problem,
            "description": "Unknown property, or not bookable online "
            "(application/problem+json).",
        },
        "default": {"description": "Other errors."},
    },
)
async def get_availability(
    env: PublicEnv,
    propertyId: int,
    checkIn: Annotated[date, Query(description="Arrival date (YYYY-MM-DD).")],
    checkOut: Annotated[
        date,
        Query(
            description="Departure date (YYYY-MM-DD). Exclusive: the last night "
            "is the day before."
        ),
    ],
    adults: Annotated[int, Query(description="Adults of the whole booking.")],
    children: Annotated[int, Query(description="Children of the whole booking.")],
    rooms: Annotated[int, Query(description="Rooms wanted.")],
) -> AvailabilityResponse:
    """Room types and rates bookable for a stay at a property, priced with
    taxes included. Public: it needs no token."""
    return (
        env["pms_booking_engine.availability_router.helper"]
        .new()
        .get_availability(propertyId, checkIn, checkOut, adults, children, rooms)
    )


@dataclass(eq=False)
class AvailabilitySearch:
    """The stay being searched, and what has been computed for it."""

    pms_property: Any
    check_in: date
    check_out: date
    adults: int
    children: int
    rooms: int
    today: date
    # Rooms free every night, by room type id
    free_rooms: dict = field(default_factory=dict)
    # Free rooms sharing space with others (parent and child rooms): their
    # family, the ids of their ancestors and descendants
    room_families: dict = field(default_factory=dict)
    shared_room_type_ids: set = field(default_factory=set)
    board_products: Any = None
    priced_board_products: dict = field(default_factory=dict)
    room_prices: dict = field(default_factory=dict)
    board_prices: dict = field(default_factory=dict)
    tourist_tax_prices: dict = field(default_factory=dict)
    unit_amounts: dict = field(default_factory=dict)
    tourist_tax_products: Any = None

    @property
    def nights(self):
        return [
            self.check_in + timedelta(days=i)
            for i in range((self.check_out - self.check_in).days)
        ]


@dataclass(eq=False)
class OfferOption:
    """A sellable row of a room type: a pricelist with a board (or none)."""

    id: str
    pricelist: Any
    room_type: Any
    board: Any
    max_quantity: int
    # Limits of the availability plan of the pricelist for the room type over
    # the stay; None means no limit. max_avail is per plan; the quota may be a
    # pool shared by every plan of the room type.
    max_avail_limit: int | None
    quota_limit: int | None

    @property
    def plan_key(self):
        return (self.pricelist.availability_plan_id.id, self.room_type.id)


@dataclass(eq=False)
class RoomTypeOption:
    room_type: Any
    capacity: int
    available_units: int
    offers: list


class PmsBookingEngineAvailabilityRouterHelper(models.AbstractModel):
    _name = "pms_booking_engine.availability_router.helper"
    _description = "PMS Booking Engine Availability Router Helper"

    def get_availability(
        self, property_id, check_in, check_out, adults, children, rooms
    ):
        # Anonymous endpoint: it reads as superuser and scopes every query to
        # the property explicitly.
        helper = self.sudo()
        pms_property = helper._get_bookable_property(property_id)
        if not pms_property:
            return helper._problem(
                404,
                "property-not-found",
                _("Property not found"),
                _("There is no property bookable online with id %s.") % property_id,
                propertyId=property_id,
            )
        helper = helper.with_company(pms_property.company_id)
        today = helper._get_property_today(pms_property)
        problem = helper._check_search(
            today, check_in, check_out, adults, children, rooms
        )
        if problem:
            return problem
        search = AvailabilitySearch(
            pms_property=pms_property,
            check_in=check_in,
            check_out=check_out,
            adults=adults,
            children=children,
            rooms=rooms,
            today=today,
        )
        return helper._get_availability_response(search)

    # Request

    def _get_bookable_property(self, property_id):
        """The property if it is sold online now: active, with an active, direct
        and on-line booking engine channel available for it."""
        pms_property = self.env["pms.property"].browse(property_id).exists()
        channel = pms_property.booking_engine_sale_channel_id
        if (
            pms_property.active
            and channel.active
            and channel.channel_type == "direct"
            and channel.is_on_line
            and (
                not channel.pms_property_ids or pms_property in channel.pms_property_ids
            )
        ):
            return pms_property
        return self.env["pms.property"]

    def _get_property_today(self, pms_property):
        return datetime.now(pytz.timezone(pms_property.tz or "UTC")).date()

    def _problem(self, status, error_type, title, detail, headers=None, **members):
        """RFC 9457 problem+json response."""
        return JSONResponse(
            status_code=status,
            media_type="application/problem+json",
            headers=headers,
            content={
                "type": f"/errors/{error_type}",
                "title": title,
                "status": status,
                "detail": detail,
                **members,
            },
        )

    def _invalid_parameter_problem(self, field_name):
        """Problem for a missing or malformed request parameter, which FastAPI
        rejects before the endpoint runs."""
        return self._problem(
            400,
            "invalid-parameter",
            _("Invalid parameter"),
            _("The parameter %s is missing or not valid.") % field_name,
            field=field_name,
        )

    def _http_error_problem(self, status, detail, headers=None):
        error_type = {404: "not-found", 405: "method-not-allowed"}.get(
            status, "http-error"
        )
        return self._problem(
            status, error_type, HTTPStatus(status).phrase, detail, headers=headers
        )

    def _check_search(self, today, check_in, check_out, adults, children, rooms):
        """Problem response for a search that cannot be served, else None."""
        if check_out <= check_in:
            return self._problem(
                400,
                "invalid-date-range",
                _("Invalid date range"),
                _("The check-out date must be later than the check-in date."),
            )
        if check_in < today:
            return self._problem(
                400,
                "check-in-in-the-past",
                _("Check-in in the past"),
                _("The check-in date is earlier than today at the property."),
                today=today.isoformat(),
            )
        if (check_out - check_in).days > MAX_NIGHTS:
            return self._problem(
                400,
                "stay-too-long",
                _("Stay too long"),
                _("A stay can last up to %s nights.") % MAX_NIGHTS,
                maxNights=MAX_NIGHTS,
            )
        if (check_in - today).days > MAX_DAYS_AHEAD:
            return self._problem(
                400,
                "date-out-of-window",
                _("Date out of window"),
                _("The check-in date can be up to %s days from today.")
                % MAX_DAYS_AHEAD,
                maxDaysAhead=MAX_DAYS_AHEAD,
            )
        return self._check_occupancy(adults, children, rooms)

    def _check_occupancy(self, adults, children, rooms):
        limits = (
            ("adults", adults, 1, MAX_ADULTS),
            ("children", children, 0, MAX_CHILDREN),
            ("rooms", rooms, 1, MAX_ROOMS),
        )
        for name, value, minimum, maximum in limits:
            if not minimum <= value <= maximum:
                return self._occupancy_problem(
                    name,
                    minimum,
                    maximum,
                    _("%(field)s must be between %(min)s and %(max)s.")
                    % {"field": name, "min": minimum, "max": maximum},
                )
        if rooms > adults:
            return self._occupancy_problem(
                "rooms", 1, adults, _("Every room needs at least one adult.")
            )
        return None

    def _occupancy_problem(self, name, minimum, maximum, detail):
        return self._problem(
            400,
            "occupancy-out-of-range",
            _("Occupancy out of range"),
            detail,
            field=name,
            min=minimum,
            max=maximum,
        )

    # Response

    def _get_availability_response(self, search):
        pricelists = self._get_published_pricelists(search.pms_property)
        room_types = self._get_room_types(search.pms_property)
        options = self._get_room_type_options(search, pricelists, room_types)
        used_pricelists = {
            offer.pricelist for option in options for offer in option.offers
        }
        return AvailabilityResponse(
            stay=self._to_stay(search),
            ratePlans=[
                self._to_rate_plan(search, pricelist)
                for pricelist in pricelists
                if pricelist in used_pricelists
            ],
            roomTypes=[self._to_room_type(search, option) for option in options],
            recommended=self._get_recommended(search, options),
        )

    def _get_published_pricelists(self, pms_property):
        """Pricelists of the engine sale channel the property can sell."""
        channel = pms_property.booking_engine_sale_channel_id
        return channel.product_pricelist_ids.filtered(
            lambda pricelist: self._is_pricelist_published(pms_property, pricelist)
        ).sorted(lambda pricelist: (pricelist.sequence, pricelist.id))

    def _is_pricelist_published(self, pms_property, pricelist):
        return bool(
            pricelist.is_pms_available
            and pricelist.availability_plan_id
            and (
                not pricelist.company_id
                or pricelist.company_id == pms_property.company_id
            )
            and (
                not pricelist.pms_property_ids
                or pms_property in pricelist.pms_property_ids
            )
        )

    def _get_room_types(self, pms_property):
        """Overnight room types with rooms at the property."""
        room_types = self.env["pms.room.type"].get_room_types_by_property(
            pms_property.id
        )
        rooms = self.env["pms.room"].search(
            [
                ("pms_property_id", "=", pms_property.id),
                ("room_type_id", "in", room_types.ids),
            ]
        )
        return room_types.filtered(
            lambda room_type: room_type.overnight_room
            and room_type in rooms.room_type_id
        ).sorted(lambda room_type: (room_type.sequence, room_type.id))

    def _get_room_type_options(self, search, pricelists, room_types):
        """Room types sellable for the stay, each with its offers."""
        if not pricelists or not room_types:
            return []
        self._load_free_rooms(search)
        plan_rules = self._get_plan_rules(
            search, pricelists.availability_plan_id, room_types
        )
        search.board_products = room_types.board_service_room_type_ids.filtered(
            lambda board: board.pms_property_id == search.pms_property
        ).board_service_line_ids.product_id
        for pricelist in pricelists:
            self._load_room_prices(search, pricelist, room_types)
        options = []
        for room_type in room_types:
            option = self._get_room_type_option(
                search, room_type, pricelists, plan_rules
            )
            if option:
                options.append(option)
        return options

    def _get_room_type_option(self, search, room_type, pricelists, plan_rules):
        available_units = len(search.free_rooms.get(room_type.id, self.env["pms.room"]))
        capacity = room_type.get_room_type_capacity(search.pms_property.id)
        if not available_units or not capacity:
            return None
        adults, children = self._get_unit_occupancy(search, capacity)
        offers = []
        for pricelist in pricelists:
            rules = plan_rules[(pricelist.availability_plan_id.id, room_type.id)]
            closed, max_avail_limit, quota_limit = self._get_plan_limits(
                search, room_type, rules
            )
            max_quantity = min(
                limit
                for limit in (available_units, max_avail_limit, quota_limit)
                if limit is not None
            )
            if closed or max_quantity < 1:
                continue
            for board in self._get_offer_boards(search, room_type, pricelist):
                offer = OfferOption(
                    id=f"{pricelist.id}-{room_type.id}-{board.id or 0}",
                    pricelist=pricelist,
                    room_type=room_type,
                    board=board,
                    max_quantity=max_quantity,
                    max_avail_limit=max_avail_limit,
                    quota_limit=quota_limit,
                )
                if self._get_unit_amount(search, offer, adults, children) is not None:
                    offers.append(offer)
        if not offers:
            return None
        return RoomTypeOption(
            room_type=room_type,
            capacity=capacity,
            available_units=available_units,
            offers=offers,
        )

    # Availability

    def _load_free_rooms(self, search):
        """Rooms free every night of the stay, by room type, and the space the
        shared ones share."""
        free_rooms = search.pms_property.with_context(
            room_type_id=False, capacity=False, ubication_id=False, amenity_ids=False
        ).get_real_free_rooms(search.check_in, search.check_out)
        grouped = defaultdict(lambda: self.env["pms.room"])
        for room in free_rooms.sorted("id"):
            grouped[room.room_type_id.id] |= room
            if room.parent_id or room.child_ids:
                search.room_families[room.id] = self._get_room_family(room)
                search.shared_room_type_ids.add(room.room_type_id.id)
        search.free_rooms = dict(grouped)

    def _get_room_family(self, room):
        """Ids of the rooms sharing space with the room: booking it makes its
        ancestors and descendants unavailable, as pms.availability decides."""
        family = set()
        parent = room.parent_id
        while parent:
            family.add(parent.id)
            parent = parent.parent_id
        children = room.child_ids
        while children:
            family.update(children.ids)
            children = children.child_ids
        return frozenset(family)

    def _get_plan_rules(self, search, plans, room_types):
        rules = self.env["pms.availability.plan.rule"].search(
            [
                ("availability_plan_id", "in", plans.ids),
                ("pms_property_id", "=", search.pms_property.id),
                ("room_type_id", "in", room_types.ids),
                ("date", ">=", search.check_in),
                ("date", "<=", search.check_out),
            ]
        )
        grouped = defaultdict(lambda: self.env["pms.availability.plan.rule"])
        for rule in rules:
            grouped[(rule.availability_plan_id.id, rule.room_type_id.id)] |= rule
        return grouped

    def _get_plan_limits(self, search, room_type, rules):
        """(closed, max_avail limit, quota limit) of an availability plan for
        the room type over the stay; a None limit means no limit.

        Without a rule on a night, the defaults of the room type apply; -1 means
        no limit, as in pms.availability.plan.rule.
        """
        plan_model = self.env["pms.availability.plan"]
        if any(
            plan_model.any_rule_applies(search.check_in, search.check_out, rule)
            for rule in rules
        ):
            return True, None, None
        rule_by_date = {rule.date: rule for rule in rules}
        max_avails, quotas = [], []
        for night in search.nights:
            rule = rule_by_date.get(night)
            max_avails.append(rule.max_avail if rule else room_type.default_max_avail)
            quotas.append(rule.quota if rule else room_type.default_quota)
        max_avail_limit = min(
            (limit for limit in max_avails if limit >= 0), default=None
        )
        quota_limit = min((limit for limit in quotas if limit >= 0), default=None)
        return False, max_avail_limit, quota_limit

    def _get_offer_boards(self, search, room_type, pricelist):
        """Boards sold with the room type under the pricelist; the first one,
        an empty recordset, is room only."""
        boards = room_type.board_service_room_type_ids.filtered(
            lambda board: board.pms_property_id == search.pms_property
            and board.pms_board_service_id.default_code
            and (not board.pricelist_ids or pricelist in board.pricelist_ids)
        )
        return [boards.browse()] + list(boards)

    # Prices

    def _get_unit_occupancy(self, search, capacity):
        """Guests one unit is priced for: the ones searched, up to its
        capacity, adults first."""
        adults = min(capacity, search.adults)
        return adults, min(capacity - adults, search.children)

    def _get_unit_amount(self, search, offer, adults, children):
        """Price of one unit of the offer for the whole stay with those
        guests, taxes included. None when a night has no price."""
        key = (offer.id, adults, children)
        if key not in search.unit_amounts:
            search.unit_amounts[key] = self._compute_unit_amount(
                search, offer, adults, children
            )
        return search.unit_amounts[key]

    def _compute_unit_amount(self, search, offer, adults, children):
        pricelist = offer.pricelist
        room_lines = []
        for night in search.nights:
            price = search.room_prices.get((pricelist.id, offer.room_type.id, night))
            if price is None:
                return None
            room_lines.append((price, 1))
        amount = self._get_billed_amount(
            search, pricelist, offer.room_type.product_id, room_lines
        )
        for line in offer.board.board_service_line_ids:
            if (adults and line.adults) or (children and line.children):
                amount += self._get_billed_amount(
                    search,
                    pricelist,
                    line.product_id,
                    self._get_board_service_lines(
                        search, pricelist, line, adults, children
                    ),
                )
        for product, lines in self._get_tourist_tax_lines(
            search, pricelist, offer.room_type, adults, children
        ).items():
            amount += self._get_billed_amount(search, pricelist, product, lines)
        return pricelist.currency_id.round(amount)

    def _get_billed_amount(self, search, pricelist, product, lines):
        """What the folio bills for some lines (unit price, quantity) of a
        product, taxes included.

        Like the folio, it rounds each unit price to the currency, as it is
        stored, and groups the lines with the same price before computing the
        taxes. An anonymous guest has no fiscal position, so the taxes are
        those of the product.
        """
        currency = pricelist.currency_id
        company = search.pms_property.company_id
        taxes = product.taxes_id.filtered(lambda tax: tax.company_id == company)
        quantities = defaultdict(float)
        for price_unit, qty in lines:
            quantities[currency.round(price_unit)] += qty
        return sum(
            taxes.compute_all(
                price_unit, currency=currency, quantity=qty, product=product
            )["total_included"]
            for price_unit, qty in quantities.items()
        )

    def _load_room_prices(self, search, pricelist, room_types):
        """Price of every room type and night under the pricelist; None for a
        night with no price set."""
        products = room_types.product_id
        for night in search.nights:
            results = self._compute_price_rule(search, pricelist, products, night)
            priced = self._get_priced_products(
                search, pricelist, products, night, results
            )
            for room_type in room_types:
                product = room_type.product_id
                search.room_prices[(pricelist.id, room_type.id, night)] = (
                    results[product.id][0] if product in priced else None
                )

    def _compute_price_rule(self, search, pricelist, products, night, qty=1.0):
        return pricelist._compute_price_rule(
            products,
            qty,
            consumption_date=night,
            pms_property_id=search.pms_property.id,
        )

    def _get_priced_products(
        self, search, pricelist, products, night, results, depth=0
    ):
        """Products whose price comes from a pricelist item.

        When no item applies, Odoo falls back to the list price of the product,
        a placeholder nobody set to sell the room that night.
        """
        items = self.env["product.pricelist.item"]
        priced = self.env["product.product"]
        chained = defaultdict(lambda: self.env["product.product"])
        for product in products:
            item = items.browse(results[product.id][1])
            if not item:
                continue
            if item.compute_price == "fixed" or item.base != "pricelist":
                priced |= product
            elif item.base_pricelist_id and depth < MAX_PRICELIST_CHAIN:
                chained[item.base_pricelist_id] |= product
        for base_pricelist, base_products in chained.items():
            base_results = self._compute_price_rule(
                search, base_pricelist, base_products, night
            )
            priced |= self._get_priced_products(
                search, base_pricelist, base_products, night, base_results, depth + 1
            )
        return priced

    def _get_board_service_lines(self, search, pricelist, line, adults, children):
        """(unit price, quantity) of the service lines a board line becomes,
        as pms.service._compute_service_line_ids creates them."""
        product = line.product_id
        if product.per_day and product.consumed_on in ("before", "after"):
            shift = 1 if product.consumed_on == "after" else 0
            dates = [night + timedelta(days=shift) for night in search.nights]
            qty = self._get_board_line_qty(line, adults, children)
        else:
            dates = [
                search.check_in
                if product.consumed_on == "checkin"
                else search.check_out
            ]
            # pms.service gives the only line its product_qty, 1 at creation
            qty = 1
        return [
            (self._get_board_line_price(search, pricelist, line, service_date), qty)
            for service_date in dates
        ]

    def _get_board_line_qty(self, line, adults, children):
        # Same quantity as pms.service._service_day_qty for a board service.
        if not line.product_id.per_person:
            return 1
        return (adults if line.adults else 0) + (children if line.children else 0)

    def _get_board_line_price(self, search, pricelist, line, service_date):
        """Unit price of a board line on a date, as
        pms.service._get_price_unit_line gets it when it creates the service:
        its product_qty, the quantity it prices with, is still 0 then."""
        product = line.product_id
        property_id = search.pms_property.id
        if (
            product.currency_id == pricelist.currency_id
            and product
            not in self._get_board_products_with_item(search, pricelist, service_date)
        ):
            # Without an item the pricelist returns the board price of the line,
            # whatever the pricelist: skip searching its items. Read it on the
            # product alone, not on its whole prefetch set.
            key = (line.id, service_date)
            if key not in search.board_prices:
                search.board_prices[key] = (
                    product.with_prefetch()
                    .with_context(
                        board_service_line_id=line.id,
                        property=property_id,
                        consumption_date=service_date,
                    )
                    .board_price
                )
            return search.board_prices[key]
        key = (pricelist.id, line.id, service_date)
        if key not in search.board_prices:
            search.board_prices[key] = pricelist._get_product_price(
                product=product.with_context(
                    board_service_line_id=line.id,
                    property=property_id,
                    consumption_date=service_date,
                ),
                quantity=0.0,
                consumption_date=service_date,
                pms_property_id=property_id,
                board_service_line_id=line.id,
            )
        return search.board_prices[key]

    def _get_board_products_with_item(self, search, pricelist, service_date):
        """Board products with a pricelist item on the date, all of them looked
        up at once."""
        key = (pricelist.id, service_date)
        if key not in search.priced_board_products:
            results = self._compute_price_rule(
                search, pricelist, search.board_products, service_date, qty=0.0
            )
            search.priced_board_products[key] = search.board_products.filtered(
                lambda product: results[product.id][1]
            )
        return search.priced_board_products[key]

    def _get_tourist_tax_lines(self, search, pricelist, room_type, adults, children):
        """(unit price, quantity) of the tourist tax lines of one unit, by
        product, as pms.reservation._compute_tourist_tax_lines creates them."""
        products = self._get_tourist_tax_products(search)
        reservations = self.env["pms.reservation"]
        lines = defaultdict(list)
        for number, night in enumerate(search.nights, start=1):
            for product in products:
                if not reservations._should_apply_product_for_night(
                    product, night, number
                ):
                    continue
                qty = (
                    self._get_tourist_tax_guests(product, adults, children)
                    if product.per_person
                    else 1
                )
                if qty:
                    price = self._get_tourist_tax_price(
                        search, pricelist, room_type, product, qty, night
                    )
                    lines[product].append((price, qty))
        return lines

    def _get_tourist_tax_products(self, search):
        if search.tourist_tax_products is None:
            search.tourist_tax_products = self.env[
                "pms.reservation"
            ]._get_tourist_tax_products(pms_property_id=search.pms_property.id)
        return search.tourist_tax_products

    def _get_tourist_tax_guests(self, product, adults, children):
        # pms.reservation._get_applicable_guest_count before any guest is
        # registered: adults meet the minimum age, declared children do not.
        if product.tourist_tax_min_age:
            return adults
        return adults + children

    def _get_tourist_tax_price(self, search, pricelist, room_type, product, qty, night):
        pms_property = search.pms_property
        company = pms_property.company_id
        key = (pricelist.id, product.id, qty, night)
        if key not in search.tourist_tax_prices:
            search.tourist_tax_prices[key] = pricelist._get_product_price(
                product=product.with_context(property=pms_property.id),
                quantity=qty,
                consumption_date=night,
                uom=product.uom_id,
                pms_property_id=pms_property.id,
            )
        # pms.reservation._get_product_price normalises it against the taxes of
        # the room.
        room_taxes = room_type.product_id.taxes_id.filtered(
            lambda tax: tax.company_id == company
        )
        return self.env["account.tax"]._fix_tax_included_price_company(
            search.tourist_tax_prices[key], product.taxes_id, room_taxes, company
        )

    # Cancellation

    def _get_cancellation(self, search, pricelist):
        deadline = self._get_free_cancellation_deadline(search, pricelist)
        if deadline is None:
            return Cancellation(type=CancellationType.nonRefundable, freeUntil=None)
        tz = pytz.timezone(search.pms_property.tz or "UTC")
        return Cancellation(
            type=CancellationType.free,
            freeUntil=tz.localize(datetime.combine(deadline, time(23, 59, 59))),
        )

    def _get_free_cancellation_deadline(self, search, pricelist):
        """Last day cancelling costs nothing, as decided by
        pms.reservation._check_cancel_penalty; None when, from today, it always
        costs."""
        rule = pricelist.cancelation_rule_id
        if not rule or not rule.penalty_late:
            return search.check_in
        if rule.is_non_refundable:
            return None
        # Compared in days, like the PMS: a huge margin, the old way to make a
        # rate non refundable, must not overflow a date.
        days_intime = max(rule.days_intime, 0)
        if (search.check_in - search.today).days < days_intime:
            return None
        return search.check_in - timedelta(days=days_intime)

    # Recommended

    def _get_recommended(self, search, options):
        """Cheapest way to lodge every guest in the rooms searched, with the
        least spare capacity.

        Each combination is priced first; only one better than the best so far
        is checked against the rooms that share space.
        """
        if not search.pms_property.booking_engine_show_recommended:
            return None
        best = None
        for combination in itertools.combinations_with_replacement(
            self._get_recommendation_options(search, options), search.rooms
        ):
            candidate = self._evaluate_combination(search, combination)
            if (
                candidate
                and (best is None or candidate[0] < best[0])
                and self._can_assign_rooms(search, combination)
            ):
                best = candidate
        if not best:
            return None
        (_surplus, total), offers = best
        items = Counter((offer.room_type.id, offer.id) for offer in offers)
        return Recommended(
            coversPeople=search.adults + search.children,
            items=[
                RecommendedItem(
                    roomTypeId=str(room_type_id), offerId=offer_id, quantity=quantity
                )
                for (room_type_id, offer_id), quantity in items.items()
            ],
            price=self._to_price(search, offers[0].pricelist.currency_id, total),
        )

    def _get_recommendation_options(self, search, options):
        """Room types the recommended rooms are searched among.

        The combinations grow fast with the room types. Past the limit, each
        capacity keeps its cheapest room types, only as many as can fill the
        rooms searched with the units their offers can sell, and then the most
        expensive ones go until the limit is met.
        """
        if self._count_combinations(search, options) <= MAX_RECOMMENDATION_COMBINATIONS:
            return options
        by_capacity = defaultdict(list)
        for option in options:
            by_capacity[option.capacity].append(option)
        kept = []
        for same_capacity in by_capacity.values():
            units = 0
            for option in sorted(
                same_capacity,
                key=lambda option: self._get_cheapest_amount(search, option),
            ):
                if units >= search.rooms:
                    break
                kept.append(option)
                # What one of its offers can sell alone: a lower bound of the
                # units of the room type a combination can take
                units += max(offer.max_quantity for offer in option.offers)
        kept.sort(key=lambda option: self._get_cheapest_amount(search, option))
        while (
            len(kept) > 1
            and self._count_combinations(search, kept) > MAX_RECOMMENDATION_COMBINATIONS
        ):
            kept.pop()
        _logger.info(
            "Booking engine: recommended rooms searched among %s of %s room types",
            len(kept),
            len(options),
        )
        return [option for option in options if option in kept]

    def _count_combinations(self, search, options):
        return math.comb(len(options) + search.rooms - 1, search.rooms)

    def _get_cheapest_amount(self, search, option):
        adults, children = self._get_unit_occupancy(search, option.capacity)
        return min(
            self._get_unit_amount(search, offer, adults, children)
            for offer in option.offers
        )

    def _evaluate_combination(self, search, combination):
        """((spare capacity, total), offers) of a combination of room types,
        or None when it cannot lodge the guests."""
        units = Counter(option.room_type.id for option in combination)
        if any(
            units[option.room_type.id] > option.available_units
            for option in combination
        ):
            return None
        capacities = [option.capacity for option in combination]
        surplus = sum(capacities) - search.adults - search.children
        if surplus < 0:
            return None
        occupancies = self._distribute_guests(search, capacities)
        if occupancies is None:
            return None
        used = Counter()
        offers = []
        total = 0.0
        for option, (adults, children) in zip(combination, occupancies, strict=True):
            offer, amount = self._get_cheapest_offer(
                search, option, adults, children, used, units
            )
            if offer is None:
                return None
            used[offer.id] += 1
            used[offer.plan_key] += 1
            offers.append(offer)
            total += amount
        return (surplus, total), offers

    def _distribute_guests(self, search, capacities):
        """Guests of each room: one adult in every room, then the other adults
        and the children filling the biggest rooms first."""
        order = sorted(range(len(capacities)), key=lambda i: -capacities[i])
        adults = [1] * len(capacities)
        children = [0] * len(capacities)
        adults_left = search.adults - len(capacities)
        children_left = search.children
        for i in order:
            extra = min(capacities[i] - adults[i], adults_left)
            adults[i] += extra
            adults_left -= extra
        for i in order:
            extra = min(capacities[i] - adults[i], children_left)
            children[i] += extra
            children_left -= extra
        if adults_left or children_left:
            return None
        return list(zip(adults, children, strict=True))

    def _get_cheapest_offer(self, search, option, adults, children, used, units):
        """Cheapest offer of the room type for those guests within the limits
        of its availability plan.

        Units already taken count by offer and by plan (max_avail is per plan).
        The quota may be a pool shared by every plan of the room type, so a
        plan with quota only takes part when all the units of the room type in
        the combination fit in it.
        """
        best_offer, best_amount = None, None
        room_type_units = units[option.room_type.id]
        for offer in option.offers:
            if used[offer.id] >= offer.max_quantity:
                continue
            if (
                offer.max_avail_limit is not None
                and used[offer.plan_key] >= offer.max_avail_limit
            ):
                continue
            if offer.quota_limit is not None and room_type_units > offer.quota_limit:
                continue
            amount = self._get_unit_amount(search, offer, adults, children)
            if amount is not None and (best_amount is None or amount < best_amount):
                best_offer, best_amount = offer, amount
        return best_offer, best_amount

    def _can_assign_rooms(self, search, combination):
        """Whether each room of the combination can get its own free room.

        A room shares space with its parent and child rooms: booking one makes
        the others unavailable, so they cannot be in the same booking.
        """
        type_ids = [option.room_type.id for option in combination]
        if not search.shared_room_type_ids.intersection(type_ids):
            return True
        candidates = [search.free_rooms[type_id].ids for type_id in type_ids]
        return self._assign_rooms(search, type_ids, candidates, [])

    def _assign_rooms(self, search, type_ids, candidates, chosen):
        """Backtracking over the free rooms of each position. Choosing a room
        removes it and its family from the later positions, so a dead end shows
        as soon as a room type runs out of rooms for its positions."""
        index = len(chosen)
        if index == len(type_ids):
            return True
        later_types = Counter(type_ids[index + 1 :])
        for room_id in candidates[index]:
            # Rooms of the same type are taken in order, to skip permutations
            if (
                index
                and type_ids[index] == type_ids[index - 1]
                and room_id <= chosen[-1]
            ):
                continue
            family = search.room_families.get(room_id, frozenset())
            later = [
                [other for other in rooms if other != room_id and other not in family]
                for rooms in candidates[index + 1 :]
            ]
            if any(
                len(later[type_ids[index + 1 :].index(type_id)]) < count
                for type_id, count in later_types.items()
            ):
                continue
            if self._assign_rooms(
                search,
                type_ids,
                candidates[: index + 1] + later,
                chosen + [room_id],
            ):
                return True
        return False

    # Schemas

    def _to_stay(self, search):
        return Stay(
            checkIn=search.check_in,
            checkOut=search.check_out,
            nights=len(search.nights),
            adults=search.adults,
            children=search.children,
            rooms=search.rooms,
        )

    def _to_rate_plan(self, search, pricelist):
        return RatePlan(
            id=str(pricelist.id),
            name=self._get_rate_plan_name(pricelist),
            cancellation=self._get_cancellation(search, pricelist),
        )

    def _to_room_type(self, search, option):
        room_type = option.room_type
        adults, children = self._get_unit_occupancy(search, option.capacity)
        amounts = {
            offer.id: self._get_unit_amount(search, offer, adults, children)
            for offer in option.offers
        }
        references = self._get_reference_offers(option.offers, amounts)
        offers = []
        for offer in option.offers:
            reference = references[self._get_reference_key(offer)]
            offers.append(
                self._to_offer(
                    search,
                    offer,
                    amounts[offer.id],
                    reference,
                    amounts[reference.id],
                    adults,
                    children,
                )
            )
        return RoomType(
            id=str(room_type.id),
            name=self._get_room_type_name(room_type),
            photos=self._get_room_type_photos(search, room_type),
            beds=self._get_room_type_beds(room_type),
            capacity=option.capacity,
            sizeM2=self._get_room_type_size(search, room_type),
            amenities=self._get_room_type_amenities(search, room_type),
            availableUnits=option.available_units,
            offers=sorted(offers, key=lambda offer: offer.price.total),
        )

    def _to_offer(
        self, search, offer, amount, reference, reference_amount, adults, children
    ):
        original, discount = self._get_discount(
            offer, amount, reference, reference_amount
        )
        return Offer(
            id=offer.id,
            ratePlanId=str(offer.pricelist.id),
            boardCode=self._get_board_code(offer.board),
            includedServices=self._get_included_services(offer, adults, children),
            price=self._to_price(
                search, offer.pricelist.currency_id, amount, original, discount
            ),
            maxQuantity=offer.max_quantity,
            unitsLeft=self._get_units_left(offer),
        )

    def _to_price(self, search, currency, total, original=None, discount=None):
        if original is not None:
            # Nullable, so not a CurrencyAmount: rounded for JSON as one is
            original = json_float_round(original, currency.decimal_places)
        return Price(
            total=total,
            original=original,
            perNight=currency.round(total / len(search.nights)),
            currency=currency.name,
            discount=discount,
            _decimal_places=currency.decimal_places,
        )

    def _get_reference_key(self, offer):
        """Offers compared with each other: same board, in the same currency."""
        return (
            offer.board.pms_board_service_id.id,
            offer.pricelist.currency_id.id,
        )

    def _get_reference_offers(self, offers, amounts):
        """The dearest offer of each reference key: the price the other rate
        plans with that board are a saving on."""
        references = {}
        for offer in offers:
            key = self._get_reference_key(offer)
            reference = references.get(key)
            if reference is None or amounts[offer.id] > amounts[reference.id]:
                references[key] = offer
        return references

    def _get_discount(self, offer, amount, reference, reference_amount):
        """Original price and discount of an offer cheaper than the reference
        one; (None, None) for the reference and any as dear as it."""
        if offer is reference or amount >= reference_amount:
            return None, None
        percent = round((reference_amount - amount) / reference_amount * 100, 1)
        if not percent:
            return None, None
        return reference_amount, Discount(
            label=_("Saving on %s") % self._get_rate_plan_name(reference.pricelist),
            percent=percent,
        )

    def _get_included_services(self, offer, adults, children):
        """What the board of the offer serves to the guests of one unit, as
        its lines are priced."""
        services = []
        products = self.env["product.product"]
        for line in offer.board.board_service_line_ids:
            product = line.product_id
            if product in products or not (
                (adults and line.adults) or (children and line.children)
            ):
                continue
            products |= product
            services.append(
                IncludedService(
                    code=product.default_code or str(product.id), label=product.name
                )
            )
        return services

    def _get_units_left(self, offer):
        """Scarcity warning: the units left when they are few."""
        if offer.max_quantity <= SCARCITY_UNITS:
            return offer.max_quantity
        return None

    # Hooks for the content of the property

    def _get_rate_plan_name(self, pricelist):
        return pricelist.name

    def _get_room_type_name(self, room_type):
        return room_type.name

    def _get_room_type_photos(self, search, room_type):
        base_url = search.pms_property.get_base_url()
        return [
            self._get_image_url(base_url, image)
            for image in room_type.get_room_type_images(search.pms_property.id)
        ]

    def _get_image_url(self, base_url, image):
        # Anonymous browsers can open it thanks to the read access of the
        # public group; ``unique`` lets them cache it until the image changes.
        unique = hashlib.sha512(str(image.write_date).encode()).hexdigest()[:7]
        return (
            f"{base_url}/web/image/{image._name}/{image.id}/image_1024"
            f"?unique={unique}"
        )

    def _get_room_type_beds(self, room_type):
        return ""

    def _get_room_type_size(self, search, room_type):
        return room_type.get_room_type_size(search.pms_property.id) or None

    def _get_room_type_amenities(self, search, room_type):
        return room_type.room_amenity_ids.filtered(
            lambda amenity: amenity.default_code
            and (
                not amenity.pms_property_ids
                or search.pms_property in amenity.pms_property_ids
            )
        ).mapped("default_code")

    def _get_board_code(self, board):
        return board.pms_board_service_id.default_code if board else ROOM_ONLY_CODE
