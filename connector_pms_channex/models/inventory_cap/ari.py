# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import re
from collections import defaultdict

from odoo import fields

from odoo.addons.component.core import Component
from odoo.addons.connector.exception import IDMissingInBackend

from ...components.adapter import ChannexAPIError
from ..common.ari import nights, runs

#: The marker ``channel.channex.inventory.cap._channex_marker`` writes.
MARKER = re.compile(r"\[roomdoo:(\d+):(\d+)\]\s*$")

#: A rule with no days applies every day; Channex may hand it back either way.
ALL_DAYS = ["mo", "tu", "we", "th", "fr", "sa", "su"]


def _date(value):
    return fields.Date.to_date(value) if value else None


def _comparable(rule):
    """What makes two rules the same rule, whatever Channex adds or omits."""
    return (
        rule.get("type"),
        int(rule["value"]) if rule.get("value") not in (None, "") else None,
        str(rule.get("start_date")),
        str(rule.get("end_date")),
        tuple(sorted(rule.get("affected_channels") or ())),
        tuple(sorted(rule.get("affected_room_types") or ())),
        tuple(sorted(rule.get("days") or ALL_DAYS)),
    )


class ChannelChannexInventoryCapAriSender(Component):
    """Publishes what agencies have set aside, as availability rules.

    Unlike availability, these cannot be sent in one call: every rule is a
    record of its own on Channex, created, changed and deleted one by one. So
    the rules wanted are worked out and compared with the ones already there,
    and only the difference is sent.

    A rule is only wanted where the agency, or the sale channel it belongs to,
    caps the room type lower than the property as a whole does: everywhere else
    the availability of the room type already says it, and Channex sells the
    lowest of the two.
    """

    _name = "channel.channex.inventory.cap.ari.sender"
    _inherit = "channel.channex.ari.sender"
    _apply_on = "channel.channex.inventory.cap"

    _ari_kind = "channel_caps"
    _ari_resource = "channel_availability_rules"

    def _ari_publish(self, caps, first, last):
        """:return: how many calls were made."""
        backend = self.backend_record
        property_external_id = backend._channex_bound_property_id()
        if backend.export_disabled or not property_external_id:
            return 0
        adapter = self.component(usage="backend.adapter")
        ours = defaultdict(list)
        for rule in adapter.search_read([("property_id", "=", property_external_id)]):
            match = MARKER.search(rule.get("title") or "")
            if match:
                ours[(int(match[1]), int(match[2]))].append(rule)
        calls = 0
        for cap in caps:
            window = self._ari_window(cap, first, last)
            if window[0] > window[1]:
                continue
            rules = ours[(cap.agency_id.id, cap.room_type_id.id)]
            calls += self._reconcile(adapter, cap, rules, window, (first, last))
        return calls

    def _reconcile(self, adapter, cap, rules, window, horizon):
        """Bring the rules of a cap in line over the pending nights.

        The nights looked at grow to cover every rule of ours they touch, so a
        rule is always rewritten whole and never left half covering nights it
        no longer should.
        """
        first, last = horizon
        touched = [
            rule
            for rule in rules
            if _date(rule.get("start_date")) <= window[1]
            and (_date(rule.get("end_date")) or last) >= window[0]
        ]
        span_from = max(
            first,
            min([window[0]] + [_date(rule["start_date"]) for rule in touched]),
        )
        span_to = min(
            last,
            max(
                [window[1]] + [_date(rule.get("end_date")) or last for rule in touched]
            ),
        )
        wanted = self._wanted(cap, span_from, span_to)
        stale = list(touched)
        missing = []
        for rule in wanted:
            same = next(
                (old for old in stale if _comparable(old) == _comparable(rule)), None
            )
            if same:
                stale.remove(same)
            else:
                missing.append(rule)
        calls = 0
        for rule in missing:
            # An old rule is rewritten rather than deleted and made again: one
            # call instead of two, and the hotel keeps seeing the same rule.
            old = stale.pop(0) if stale else None
            calls += self._put(adapter, old, rule) if old else self._post(adapter, rule)
        for old in stale:
            calls += self._delete(adapter, old)
        return calls

    def _wanted(self, cap, date_from, date_to):
        """The rules this cap should have between these nights."""
        channels = cap._channex_channels()
        room_type_binding = cap._channex_room_type_binding()
        if not channels or not room_type_binding:
            return []
        property_id = self.backend_record.pms_property_id.id
        room_type_id = cap.room_type_id.id
        agency = cap.agency_id
        inventory = self.env["pms.inventory.rule"]
        general = inventory.get_inventory_caps(
            property_id, date_from, date_to, [room_type_id]
        )
        scoped = inventory.get_inventory_caps(
            property_id,
            date_from,
            date_to,
            [room_type_id],
            sale_channel_id=agency.sale_channel_id.id or None,
            agency_id=agency.id,
        )
        per_night = []
        for night in nights(date_from, date_to):
            value = scoped.get((room_type_id, night))
            limit = general.get((room_type_id, night))
            stricter = value is not None and (limit is None or value < limit)
            per_night.append((night, value if stricter else None))
        wanted = []
        for run_from, run_to, value in runs(per_night):
            if value is None:
                continue
            wanted.append(
                {
                    "title": cap._channex_title(run_from, run_to),
                    # Closed and capped are two different kinds of rule.
                    "type": "close_out" if value == 0 else "max_availability",
                    "value": None if value == 0 else value,
                    "affected_channels": channels,
                    "affected_room_types": [room_type_binding.external_id],
                    "start_date": fields.Date.to_string(run_from),
                    "end_date": fields.Date.to_string(run_to),
                    "days": ALL_DAYS,
                    "property_id": self.backend_record._channex_bound_property_id(),
                }
            )
        return wanted

    # -- one call each, and what Channex refuses is kept, not raised ---------
    #
    # A rule refused must not take the others down with it: the job would be
    # retried with the ones already made, and refused again on the same one.

    @staticmethod
    def _payload(rule):
        return {key: value for key, value in rule.items() if value is not None}

    def _post(self, adapter, rule):
        try:
            adapter.create(self._payload(rule))
        except ChannexAPIError as error:
            self._ari_warn([{**rule, "warning": str(error)}])
        return 1

    def _put(self, adapter, old, rule):
        try:
            adapter.write(old["id"], self._payload(rule))
        except IDMissingInBackend:
            # Gone since it was read: what is wanted is made anew.
            return 1 + self._post(adapter, rule)
        except ChannexAPIError as error:
            self._ari_warn([{**rule, "warning": str(error)}])
        return 1

    def _delete(self, adapter, old):
        try:
            adapter.delete(old["id"])
        except IDMissingInBackend:
            # Already gone, which is what was wanted.
            pass
        return 1
