# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component
from odoo.addons.component_event.components.event import skip_if

#: What of a pricelist its rate plans are made of: the title, the currency,
#: which board services it sells, and whether it is sold here at all.
_RATE_PLAN_FIELDS = {
    "name",
    "currency_id",
    "board_service_ids",
    "pms_property_ids",
    "active",
}


class ChannelChannexProductPricelistListener(Component):
    """Keeps the rate plans of a pricelist in step with it."""

    _name = "channel.channex.product.pricelist.listener"
    _inherit = "base.connector.listener"
    _apply_on = "product.pricelist"

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_write(self, record, fields=None):
        if _RATE_PLAN_FIELDS & set(fields or ()):
            # As superuser: anyone editing a pricelist can get here.
            record.sudo()._channex_backends()._channex_schedule_rate_plans()
        if "availability_plan_id" in (fields or ()):
            # Sold under another plan, it has its restrictions.
            record._channex_stage_restrictions()
