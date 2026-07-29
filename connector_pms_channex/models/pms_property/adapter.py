# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component


class ChannelChannexPmsPropertyAdapter(Component):
    _name = "channel.channex.pms.property.adapter"
    _inherit = "channel.channex.adapter"
    _apply_on = "channel.channex.pms.property"

    _resource = "properties"
    _payload_root = "property"
    _server_filters = ("title", "group_id")
