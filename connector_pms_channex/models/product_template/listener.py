# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component
from odoo.addons.component_event.components.event import skip_if

#: What of a board service its rate plans are made of: the title, what they
#: say is included, and whether the board service is sold here at all.
_RATE_PLAN_FIELDS = {
    "name",
    "channex_meal_type",
    "is_board_service",
    "pms_property_ids",
    "active",
}


class ChannelChannexProductTemplateListener(Component):
    """Keeps the rate plans of a board service in step with it."""

    _name = "channel.channex.product.template.listener"
    _inherit = "base.connector.listener"
    _apply_on = "product.template"

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_write(self, record, fields=None):
        if not _RATE_PLAN_FIELDS & set(fields or ()):
            return
        if not record.is_board_service and "is_board_service" not in fields:
            return
        # As superuser: anyone editing a product can get here, and the
        # backends are for connector managers to see.
        self.env["channel.channex.backend"].sudo().search(
            []
        )._channex_schedule_rate_plans()
