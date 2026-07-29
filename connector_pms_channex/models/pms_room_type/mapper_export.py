# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import _
from odoo.exceptions import ValidationError

from odoo.addons.component.core import Component
from odoo.addons.connector.components.mapper import mapping


class ChannelChannexPmsRoomTypeExportMapper(Component):
    _name = "channel.channex.pms.room.type.export.mapper"
    _inherit = "channel.channex.export.mapper"
    _apply_on = "channel.channex.pms.room.type"

    @mapping
    def identity(self, record):
        if record.default_occupancy > record.occ_adults:
            # Channex rejects this, so it is caught before spending a call.
            raise ValidationError(
                _(
                    "Room type %s has a default occupancy larger than its "
                    "number of adults."
                )
                % record.odoo_id.name
            )
        values = {
            "property_id": record._channex_property_external_id(),
            "title": record.odoo_id.name,
            "count_of_rooms": record.count_of_rooms,
            "occ_adults": record.occ_adults,
            "occ_children": record.occ_children,
            "occ_infants": record.occ_infants,
            "default_occupancy": record.default_occupancy,
            "room_kind": record.room_kind,
        }
        if record.room_kind == "dorm":
            values["capacity"] = record.capacity
        return values
