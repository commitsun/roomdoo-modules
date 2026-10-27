# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component
from odoo.addons.component_event.components.event import skip_if

from .pms_availability_plan_board_rule import BOARD_RULE_FIELDS


class ChannelChannexPmsAvailabilityPlanBoardRuleListener(Component):
    """A board service closed is the rate plans that sell it closed."""

    _name = "channel.channex.pms.availability.plan.board.rule.listener"
    _inherit = "base.connector.listener"
    _apply_on = "pms.availability.plan.board.rule"

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_create(self, record, fields=None):
        record._channex_stage_restrictions()

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_write(self, record, fields=None):
        if fields and set(fields) & BOARD_RULE_FIELDS:
            record._channex_stage_restrictions()

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_unlink(self, record, fields=None):
        # Read before the rule is gone: deleting it opens what it closed.
        record._channex_stage_restrictions()
