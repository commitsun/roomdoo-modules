# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component


class ChannelChannexChannelAdapter(Component):
    """Channels are created in the Channex UI, never from here.

    Writes are limited to reaching into an existing mapping to set the rate
    logic; the mapping itself, and the room and rate codes in it, stay the
    hotel's.

    Not a binding of any PMS model, so it works on the Channex-side model
    directly.
    """

    _name = "channel.channex.channel.adapter"
    _inherit = "channel.channex.adapter"
    _apply_on = "channel.channex.channel"

    _resource = "channels"
    _payload_root = "channel"
    # An account holds the channels of every one of its properties, so the
    # property filter is not an optimisation, it is the scoping.
    _server_filters = ("property_id", "channel", "is_active")
