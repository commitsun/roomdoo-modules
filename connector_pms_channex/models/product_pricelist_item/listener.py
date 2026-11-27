# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component
from odoo.addons.component_event.components.event import skip_if

from .product_pricelist_item import PRICE_FIELDS


class ChannelChannexProductPricelistItemListener(Component):
    """A pricelist item is a price of the rate plans of its pricelist, and of
    those of every pricelist priced from it."""

    _name = "channel.channex.product.pricelist.item.listener"
    _inherit = "base.connector.listener"
    _apply_on = "product.pricelist.item"

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_create(self, record, fields=None):
        record._channex_stage_restrictions()

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_write(self, record, fields=None):
        if fields and set(fields) & PRICE_FIELDS:
            record._channex_stage_restrictions()

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_unlink(self, record, fields=None):
        # Read before the item is gone: deleting it moves the price back to
        # whatever priced those nights before.
        record._channex_stage_restrictions()
