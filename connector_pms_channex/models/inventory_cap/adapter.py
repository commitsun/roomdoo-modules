# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component


class ChannelChannexInventoryCapAdapter(Component):
    """Channex's availability rules: a cap, or a close out, on some channels
    and room types over a range of dates."""

    _name = "channel.channex.inventory.cap.adapter"
    _inherit = "channel.channex.adapter"
    _apply_on = "channel.channex.inventory.cap"

    _resource = "channel_availability_rules"
    _payload_root = "channel_availability_rule"
    _server_filters = ("property_id",)
