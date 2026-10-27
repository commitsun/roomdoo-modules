# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component
from odoo.addons.component_event.components.event import skip_if


class ChannelChannexPmsAvailabilityPlanListener(Component):
    """A plan given another parent inherits other rules, and so does every
    plan below it."""

    _name = "channel.channex.pms.availability.plan.listener"
    _inherit = "base.connector.listener"
    _apply_on = "pms.availability.plan"

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_write(self, record, fields=None):
        if "parent_id" in (fields or ()):
            descendants = record._channex_descendants()
            descendants.pms_pricelist_ids._channex_stage_restrictions()
