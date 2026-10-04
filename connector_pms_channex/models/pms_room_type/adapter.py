# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component


class ChannelChannexPmsRoomTypeAdapter(Component):
    _name = "channel.channex.pms.room.type.adapter"
    _inherit = "channel.channex.adapter"
    _apply_on = "channel.channex.pms.room.type"

    _resource = "room_types"
    _payload_root = "room_type"
    _server_filters = ("property_id", "title")
