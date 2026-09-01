# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component
from odoo.addons.component_event.components.event import skip_if


class ChannelChannexPmsCancelationRuleListener(Component):
    """Keeps the policy in step with the rule it was made from.

    Any change to the rule is pushed, unlike a room type: the whole point of the
    policy is to say on the OTA what the hotel will charge, so a rule that says
    something else is worse than no rule at all.
    """

    _name = "channel.channex.pms.cancelation.rule.listener"
    _inherit = "base.connector.listener"
    _apply_on = "pms.cancelation.rule"

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_create(self, record, fields=None):
        record._channex_schedule_sync()

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_write(self, record, fields=None):
        record._channex_schedule_sync()


class ChannelChannexProductPricelistListener(Component):
    """Which rule a property answers to is written on its pricelist, so a
    pricelist changing rule changes which policy the property names."""

    _name = "channel.channex.product.pricelist.listener"
    _inherit = "base.connector.listener"
    _apply_on = "product.pricelist"

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_write(self, record, fields=None):
        if not fields or "cancelation_rule_id" not in fields:
            return
        record.cancelation_rule_id._channex_schedule_sync()


class ChannelChannexPmsPropertyCancelationListener(Component):
    """And a property changing its default pricelist changes it too."""

    _name = "channel.channex.pms.property.cancelation.listener"
    _inherit = "base.connector.listener"
    _apply_on = "pms.property"

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_write(self, record, fields=None):
        if not fields or "default_pricelist_id" not in fields:
            return
        record.default_pricelist_id.cancelation_rule_id._channex_schedule_sync()
