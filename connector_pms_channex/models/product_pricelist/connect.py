# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component


class ChannelChannexProductPricelistConnectCandidates(Component):
    """A pricelist is a rate plan for each room type and board service on
    Channex, never one record there to connect it to: connecting it is creating
    them. The rate plans the hotel already made by hand with the same names are
    taken over then, so they keep what is mapped to them."""

    _name = "channel.channex.product.pricelist.connect.candidates"
    _inherit = "channel.channex.connect.candidates"
    _apply_on = "channel.channex.product.pricelist"

    _connects_to_existing = False

    def fetch(self):
        return []
