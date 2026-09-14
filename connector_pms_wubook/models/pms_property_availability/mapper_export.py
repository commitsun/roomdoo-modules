# Copyright 2021 Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import datetime

from odoo.addons.component.core import Component
from odoo.addons.connector.components.mapper import mapping


class ChannelWubookPmsPropertyAvailabilityMapperExport(Component):
    _name = "channel.wubook.pms.property.availability.mapper.export"
    _inherit = "channel.wubook.mapper.export"

    _apply_on = "channel.wubook.pms.property.availability"

    @mapping
    def availabilities(self, record):
        """How many rooms of each type are on sale, night by night.

        The number is resolved here rather than read off a record: it is the
        physical availability of the night capped by the inventory declared
        for it, and neither of those is stored per night any more. A night
        nothing has touched has no ``pms.availability`` row at all, and it
        still has rooms to sell, which is exactly what the resolver falls
        back to.
        """
        window = record._wubook_export_window()
        if not window:
            return None
        date_from, date_to = window
        room_type_bindings = record._wubook_export_room_types()
        if not room_type_bindings:
            return None
        room_type_ids = room_type_bindings.mapped("odoo_id").ids
        pms_property_id = record.backend_id.pms_property_id.id
        real_avail = self.env["pms.availability"].get_real_avail_map(
            pms_property_id, date_from, date_to, room_type_ids=room_type_ids
        )
        # The property as a whole, not a channel of its own: what Wubook
        # sells is the inventory declared at the general scope.
        caps = self.env["pms.inventory.rule"].get_inventory_caps(
            pms_property_id, date_from, date_to, room_type_ids=room_type_ids
        )
        items = []
        for offset in range((date_to - date_from).days + 1):
            date = date_from + datetime.timedelta(days=offset)
            for binding in room_type_bindings:
                room_type_id = binding.odoo_id.id
                cap = caps.get((room_type_id, date))
                if cap is None:
                    # Nothing declared for this night: the ceiling is the
                    # availability the connector defaults to for the type.
                    cap = binding.default_availability
                items.append(
                    {
                        "date": date,
                        "id_room": binding.external_id,
                        "avail": min(real_avail[(room_type_id, date)], cap),
                    }
                )
        return {"availabilities": items}
