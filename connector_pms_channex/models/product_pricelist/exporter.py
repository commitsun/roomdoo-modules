# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component


class ChannelChannexProductPricelistExporter(Component):
    """Exporting a pricelist is bringing the rate plans of the property in
    line, there and then: a pricelist just connected has its rate plans on
    Channex when the connection is made, and anything Channex refuses is
    refused to whoever connected it."""

    _name = "channel.channex.product.pricelist.exporter"
    _inherit = "channel.channex.exporter"
    _apply_on = "channel.channex.product.pricelist"

    def run(self, relation, *args, **kwargs):
        return self.backend_record.channex_sync_rate_plans()
