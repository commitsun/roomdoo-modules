# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import fields

from odoo.addons.component.core import Component

from ..common.ari import nights, runs


class ChannelChannexPmsRoomTypeAriSender(Component):
    """Publishes the availability of the room types.

    What a room type has for sale on a night is what pms says a hotel can sell
    of it through any channel: the rooms free that night, capped by the
    inventory declared for the property as a whole. What an agency or a channel
    has set aside is a cap of its own, published apart, and Channex applies the
    lowest of the two.

    Sent as the number itself, never as a change: Channex takes a booking off
    the availability on its own, and doing it again here would count it twice.
    """

    _name = "channel.channex.pms.room.type.ari.sender"
    _inherit = "channel.channex.ari.sender"
    _apply_on = "channel.channex.pms.room.type"

    _ari_kind = "availability"
    _ari_resource = "availability"

    def _ari_values(self, bindings, first, last):
        windows = {}
        for binding in bindings:
            if not binding.external_id or not binding.active or binding._is_excluded():
                continue
            window = self._ari_window(binding, first, last)
            if window[0] <= window[1]:
                windows[binding] = window
        if not windows:
            return []
        backend = self.backend_record
        property_id = backend.pms_property_id.id
        date_from = min(window[0] for window in windows.values())
        date_to = max(window[1] for window in windows.values())
        room_type_ids = [binding.odoo_id.id for binding in windows]
        real = self.env["pms.availability"].get_real_avail_map(
            property_id, date_from, date_to, room_type_ids
        )
        caps = self.env["pms.inventory.rule"].get_inventory_caps(
            property_id, date_from, date_to, room_type_ids
        )
        property_external_id = backend._channex_bound_property_id()
        values = []
        for binding, (window_from, window_to) in windows.items():
            room_type_id = binding.odoo_id.id
            per_night = []
            for night in nights(window_from, window_to):
                available = real[(room_type_id, night)]
                cap = caps.get((room_type_id, night))
                if cap is not None:
                    available = min(available, cap)
                per_night.append((night, max(available, 0)))
            for run_from, run_to, available in runs(per_night):
                values.append(
                    {
                        "property_id": property_external_id,
                        "room_type_id": binding.external_id,
                        "date_from": fields.Date.to_string(run_from),
                        "date_to": fields.Date.to_string(run_to),
                        "availability": available,
                    }
                )
        return values
