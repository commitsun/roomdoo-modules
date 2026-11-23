# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from collections import defaultdict

from odoo import fields

from odoo.addons.component.core import Component

from ..common.ari import nights, runs
from ..common.board import board_total, board_units


class ChannelChannexRatePlanAriSender(Component):
    """Publishes the prices and restrictions of the rate plans.

    Every night of a rate plan gets a price for each number of adults its room
    type holds: the room for that many adults under the pricelist, plus the
    board service for them, worked out as pms works out a reservation. And it
    gets the restrictions of the pricelist's availability plan, inherited from
    its parents, closed where the plan closes its board service. A night the
    plan says nothing about goes as unrestricted, so that a restriction lifted
    is lifted on Channex too.

    A night that comes out at no price is closed instead: Channex takes no
    price of 0, and it must not go on selling at the one it had.
    """

    _name = "channel.channex.rate.plan.ari.sender"
    _inherit = "channel.channex.ari.sender"
    _apply_on = "channel.channex.rate.plan"

    _ari_kind = "restrictions"
    _ari_resource = "restrictions"

    def _ari_values(self, bindings, first, last):
        windows = {}
        for rate_plan in bindings:
            if not rate_plan.external_id or rate_plan.channex_to_remove:
                continue
            window = self._ari_window(rate_plan, first, last)
            if window[0] <= window[1]:
                windows[rate_plan] = window
        by_pricelist = defaultdict(list)
        for rate_plan in windows:
            by_pricelist[rate_plan.pricelist_id].append(rate_plan)
        # The price of a room or a board service on a night is the same for
        # every rate plan it is part of, so each is worked out once.
        self._room_prices = {}
        self._board_prices = {}
        self._property_external_id = self.backend_record._channex_bound_property_id()
        values = []
        for pricelist, rate_plans in by_pricelist.items():
            date_from = min(windows[rate_plan][0] for rate_plan in rate_plans)
            date_to = max(windows[rate_plan][1] for rate_plan in rate_plans)
            rules, board_rules = self._restrictions(
                pricelist,
                date_from,
                date_to,
                [rate_plan.room_type_binding_id.odoo_id.id for rate_plan in rate_plans],
            )
            for rate_plan in rate_plans:
                per_night = [
                    (night, self._night(rate_plan, night, rules, board_rules))
                    for night in nights(*windows[rate_plan])
                ]
                for run_from, run_to, night in runs(per_night):
                    values.append(self._value(rate_plan, run_from, run_to, night))
        return values

    def _restrictions(self, pricelist, date_from, date_to, room_type_ids):
        """The rule that decides each night, of the room types and of the
        board services, under the availability plan of the pricelist."""
        plan = pricelist.availability_plan_id
        if not plan:
            return {}, {}
        property_id = self.backend_record.pms_property_id.id
        rules = self.env["pms.availability.plan.rule"]._resolve_rules(
            plan.id, property_id, date_from, date_to, room_type_ids
        )
        board_rules = self.env["pms.availability.plan.board.rule"]._resolve_rules(
            plan.id, property_id, date_from, date_to
        )
        return rules, board_rules

    def _night(self, rate_plan, night, rules, board_rules):
        """What a rate plan holds on a night, comparable with the next one."""
        rule = rules.get((rate_plan.room_type_binding_id.odoo_id.id, night))
        board_rule = board_rules.get((rate_plan.board_service_id.id, night))
        rates = self._rates(rate_plan, night)
        closed = not rates or bool(board_rule and board_rule.closed)
        if not rule:
            return (rates, 1, 1, 0, False, False, closed)
        max_stays = [stay for stay in (rule.max_stay, rule.max_stay_arrival) if stay]
        return (
            rates,
            rule.min_stay_arrival or 1,
            rule.min_stay or 1,
            # Channex has one max stay, which each channel applies on arrival
            # or through the stay: the stricter of the two pms has holds both.
            min(max_stays) if max_stays else 0,
            rule.closed_arrival,
            rule.closed_departure,
            closed or rule.closed,
        )

    def _rates(self, rate_plan, night):
        """The price for each number of adults, or ``None`` when any of them
        comes out at no price."""
        pricelist = rate_plan.pricelist_id
        room_type = rate_plan.room_type_binding_id.odoo_id
        board_service = rate_plan.board_service_id
        rates = []
        for occupancy in range(1, rate_plan.room_type_binding_id.occ_adults + 1):
            price = pricelist.currency_id.round(
                self._room_price(pricelist, room_type, night, occupancy)
                + self._board_price(pricelist, board_service, night, occupancy)
            )
            if price <= 0:
                return None
            rates.append((occupancy, f"{price:.2f}"))
        return tuple(rates) or None

    def _room_price(self, pricelist, room_type, night, occupancy):
        """The room for that many adults, children aside: Channex charges them
        with the rate plan's children fee."""
        key = (pricelist.id, room_type.id, night, occupancy)
        if key not in self._room_prices:
            self._room_prices[key] = pricelist._get_product_price(
                room_type.product_id,
                1,
                consumption_date=night,
                pms_property_id=self.backend_record.pms_property_id.id,
                occupancy=occupancy,
            )
        return self._room_prices[key]

    def _board_price(self, pricelist, board_service, night, adults):
        """The board service for that many adults, children aside, as a
        booking of this rate plan will take it off the room."""
        key = (pricelist.id, board_service.id, night)
        if key not in self._board_prices:
            self._board_prices[key] = board_units(
                pricelist, board_service, self.backend_record.pms_property_id, night
            )
        return board_total(self._board_prices[key], adults, 0)

    def _value(self, rate_plan, date_from, date_to, night):
        rates, min_arrival, min_through, max_stay, arrival, departure, closed = night
        value = {
            "property_id": self._property_external_id,
            "rate_plan_id": rate_plan.external_id,
            "date_from": fields.Date.to_string(date_from),
            "date_to": fields.Date.to_string(date_to),
            "min_stay_arrival": min_arrival,
            "min_stay_through": min_through,
            "max_stay": max_stay,
            "closed_to_arrival": arrival,
            "closed_to_departure": departure,
            "stop_sell": closed,
        }
        if rates:
            value["rates"] = [
                {"occupancy": occupancy, "rate": rate} for occupancy, rate in rates
            ]
        return value
