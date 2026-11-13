# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component
from odoo.addons.component_event.components.event import skip_if

from .pms_inventory_rule import INVENTORY_FIELDS


class ChannelChannexPmsInventoryRuleListener(Component):
    """The inventory declared for a property caps its availability, and the one
    declared for an agency or a sale channel caps what they sell. It lives in a
    model nothing else watched: this is what tells Channex a cap moved."""

    _name = "channel.channex.pms.inventory.rule.listener"
    _inherit = "base.connector.listener"
    _apply_on = "pms.inventory.rule"

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_create(self, record, fields=None):
        record._channex_stage()

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_write(self, record, fields=None):
        if fields and set(fields) & INVENTORY_FIELDS:
            record._channex_stage()

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_unlink(self, record, fields=None):
        # Read before the rule is gone: deleting it lifts its cap.
        record._channex_stage()
