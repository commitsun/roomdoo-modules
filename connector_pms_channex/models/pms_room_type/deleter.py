# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component


class ChannelChannexPmsRoomTypeDeleter(Component):
    _name = "channel.channex.pms.room.type.deleter"
    _inherit = "channel.channex.deleter"
    _apply_on = "channel.channex.pms.room.type"
