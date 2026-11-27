# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component
from odoo.addons.component_event.components.event import skip_if

from .pms_pricelist_occupancy import PRICE_FIELDS


class ChannelChannexPmsPricelistOccupancyListener(Component):
    """What a pricelist charges for each child is a figure of its rate plans,
    and what it charges for each adult more or less is a price of them."""

    _name = "channel.channex.pms.pricelist.occupancy.listener"
    _inherit = "base.connector.listener"
    _apply_on = "pms.pricelist.occupancy"

    def _schedule(self, record):
        # As superuser: anyone editing a pricelist can get here.
        record.sudo().pricelist_id._channex_backends()._channex_schedule_rate_plans()

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_create(self, record, fields=None):
        self._schedule(record)
        record._channex_stage_restrictions()

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_write(self, record, fields=None):
        if {"children_fee", "pricelist_id", "room_type_id"} & set(fields or ()):
            self._schedule(record)
        if PRICE_FIELDS & set(fields or ()):
            record._channex_stage_restrictions()

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_unlink(self, record, fields=None):
        self._schedule(record)
        record._channex_stage_restrictions()
