# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component
from odoo.addons.component_event.components.event import skip_if

from .pms_availability_plan_rule import RESTRICTION_FIELDS


class ChannelChannexPmsAvailabilityPlanRuleListener(Component):
    """The restrictions of a plan are those of the rate plans of every
    pricelist sold under it, or under a plan inheriting from it."""

    _name = "channel.channex.pms.availability.plan.rule.listener"
    _inherit = "base.connector.listener"
    _apply_on = "pms.availability.plan.rule"

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_create(self, record, fields=None):
        record._channex_stage_restrictions()

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_write(self, record, fields=None):
        if fields and set(fields) & RESTRICTION_FIELDS:
            record._channex_stage_restrictions()

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_unlink(self, record, fields=None):
        # Read before the rule is gone: deleting it lifts what it restricted.
        record._channex_stage_restrictions()
