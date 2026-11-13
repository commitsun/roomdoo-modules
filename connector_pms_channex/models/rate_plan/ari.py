# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from collections import defaultdict

from odoo import fields

from odoo.addons.component.core import Component

from ..common.ari import nights, runs
from ..common.board import board_total, board_units

#: What a night of a rate plan carries, in the order ``_night`` gives it.
FIELDS = (
    "rates",
    "min_stay_arrival",
    "min_stay_through",
    "max_stay",
    "closed_to_arrival",
    "closed_to_departure",
    "stop_sell",
)


def _as_night(held):
    """A night as kept in ``channex_ari_held``, in the shape ``_night`` gives
    it: what JSON keeps as lists, the night has as tuples."""
    rates, *restrictions = held
    return (tuple(map(tuple, rates)) if rates else None, *restrictions)


class ChannelChannexRatePlanAriSender(Component):
    """Publishes the prices and restrictions of the rate plans.

    Every night of a rate plan gets a price for each number of adults its room
    type holds: the room for that many adults under the pricelist, plus the
    board service for them, worked out as pms works out a reservation. And it
    gets the restrictions of the pricelist's availability plan, inherited from
    its parents, closed where the plan closes its board service. A night the
    plan says nothing about is unrestricted, so that a restriction lifted is
    lifted on Channex too.

    A night that comes out at no price is closed instead: Channex takes no
    price of 0, and it must not go on selling at the one it had.

    Of all that, only what Channex does not hold yet is sent. Channex's
    certification asks a PMS to send changes, and a night sent whole when only
    its price moved is not one.
    """

    _name = "channel.channex.rate.plan.ari.sender"
    _inherit = "channel.channex.ari.sender"
    _apply_on = "channel.channex.rate.plan"

    _ari_kind = "restrictions"
    _ari_resource = "restrictions"

    def _ari_publish(self, rate_plans, first, last):
        """Send what changed of these rate plans since Channex last took them.

        What goes out is recorded, and the window cleared, before the call, and
        not after it as the other kinds clear theirs: those writes hold these
        rows until the call is over. A change noted since this send began
        clashes with them and makes the send start again before anything goes
        out, and one noted during the call waits for it. Recorded after the
        call, a clash would undo the record of a call that did go out, and a
        change undone straight away would then match the record and never reach
        Channex.

        Nothing is recorded on a backend with exports disabled, as nothing
        leaves it.
        """
        values, held = self._ari_changes(rate_plans, first, last)
        record = not self.backend_record.export_disabled
        for rate_plan in rate_plans:
            changes = {
                "channex_ari_pending_from": False,
                "channex_ari_pending_to": False,
            }
            if record and rate_plan in held:
                changes["channex_ari_held"] = held[rate_plan]
            rate_plan.write(changes)
        rate_plans.flush_recordset()
        if values:
            self._ari_send(values)
        return len(values)

    def _ari_changes(self, rate_plans, first, last):
        """What to send of these rate plans, and what Channex holds of them
        once it is sent.

        Every night of the window is worked out whole and compared with what
        Channex holds of it, and only the fields that differ go out: a night
        that is the same goes nowhere, and nights in a row with the same change
        go as one range, however else they differ. A night nothing is known
        Channex holds -- a rate plan just made, a night just in the window, a
        full sync -- goes whole.

        :return: ``(values, held)``, ``held`` by rate plan.
        """
        windows = {}
        for rate_plan in rate_plans:
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
        values, held = [], {}
        first_night = fields.Date.to_string(first)
        for pricelist, plans in by_pricelist.items():
            date_from = min(windows[rate_plan][0] for rate_plan in plans)
            date_to = max(windows[rate_plan][1] for rate_plan in plans)
            rules, board_rules = self._restrictions(
                pricelist,
                date_from,
                date_to,
                [rate_plan.room_type_binding_id.odoo_id.id for rate_plan in plans],
            )
            for rate_plan in plans:
                before = rate_plan.channex_ari_held or {}
                # The nights gone by are dropped: Channex sells them no more.
                after = {
                    night: value
                    for night, value in before.items()
                    if night >= first_night
                }
                per_night = []
                for night in nights(*windows[rate_plan]):
                    key = fields.Date.to_string(night)
                    now = self._night(rate_plan, night, rules, board_rules)
                    per_night.append((night, self._changes(now, before.get(key))))
                    after[key] = now
                held[rate_plan] = after
                for run_from, run_to, changes in runs(per_night):
                    value = self._value(rate_plan, run_from, run_to, changes)
                    if value:
                        values.append(value)
        return values, held

    @staticmethod
    def _changes(night, held):
        """The fields of a night that Channex does not hold as they are: every
        one, when nothing is known it holds of the night."""
        if held is None:
            return tuple(zip(FIELDS, night, strict=True))
        return tuple(
            (field, value)
            for field, value, was in zip(FIELDS, night, _as_night(held), strict=True)
            if value != was
        )

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
        return (
            rates,
            rule.min_stay_arrival or 1,
            rule.min_stay or 1,
            # Channex has no max stay on arrival, and nothing sold there has one.
            rule.max_stay,
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

    def _value(self, rate_plan, date_from, date_to, changes):
        """A range of nights with what changed in them, or ``None`` when that
        leaves nothing to send: a night that lost its price is closed, and the
        lost price is not sent."""
        value = dict(changes)
        rates = value.pop("rates", None)
        if rates:
            value["rates"] = [
                {"occupancy": occupancy, "rate": rate} for occupancy, rate in rates
            ]
        if not value:
            return None
        return {
            "property_id": self._property_external_id,
            "rate_plan_id": rate_plan.external_id,
            "date_from": fields.Date.to_string(date_from),
            "date_to": fields.Date.to_string(date_to),
            **value,
        }

    def _ari_refused(self, values):
        """Forget what Channex holds of the nights of a call it refused part
        of, so that they go whole the next time anything moves in them.

        The warnings say which values were refused, but forgetting the whole
        call does not depend on how they say it, and costs no more than a few
        nights sent again.
        """
        by_rate_plan = defaultdict(list)
        for value in values:
            by_rate_plan[value["rate_plan_id"]].append(value)
        rate_plans = self.model.search(
            [
                ("backend_id", "=", self.backend_record.id),
                ("external_id", "in", list(by_rate_plan)),
            ]
        )
        for rate_plan in rate_plans:
            held = dict(rate_plan.channex_ari_held or {})
            for value in by_rate_plan[rate_plan.external_id]:
                for night in nights(
                    fields.Date.to_date(value["date_from"]),
                    fields.Date.to_date(value["date_to"]),
                ):
                    held.pop(fields.Date.to_string(night), None)
            rate_plan.channex_ari_held = held
