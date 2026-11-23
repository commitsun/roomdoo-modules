# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component


class ChannelChannexRatePlanAdapter(Component):
    _name = "channel.channex.rate.plan.adapter"
    _inherit = "channel.channex.adapter"
    _apply_on = "channel.channex.rate.plan"

    _resource = "rate_plans"
    _payload_root = "rate_plan"
    _server_filters = ("property_id",)

    def _unwrap(self, data):
        """Channex takes the room type of a rate plan among its attributes, and
        hands it back among its relationships."""
        record = super()._unwrap(data)
        if data:
            room_type = ((data.get("relationships") or {}).get("room_type") or {}).get(
                "data"
            ) or {}
            record["room_type_id"] = room_type.get("id")
        return record
