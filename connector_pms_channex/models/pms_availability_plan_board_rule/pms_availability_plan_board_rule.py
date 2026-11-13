# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import models

#: Every field of a rule: any write to one makes the rule the one written last,
#: which is the one that wins where rules overlap.
BOARD_RULE_FIELDS = {
    "availability_plan_id",
    "pms_property_id",
    "board_service_id",
    "date_from",
    "date_to",
    "closed",
}


class PmsAvailabilityPlanBoardRule(models.Model):
    _inherit = "pms.availability.plan.board.rule"

    def _channex_stage_restrictions(self):
        """Publish again the nights these rules close or open, on the rate
        plans of their board service of every pricelist of their plan. Not of
        the plans inheriting from it: pms does not pass these rules on."""
        for rule in self:
            rule.availability_plan_id.pms_pricelist_ids._channex_stage_restrictions(
                board_service=rule.board_service_id,
                date_from=rule.date_from,
                date_to=rule.date_to,
                pms_properties=rule.pms_property_id,
            )

    def write(self, vals):
        if BOARD_RULE_FIELDS & set(vals) and not self.env.context.get(
            "connector_no_export"
        ):
            # A rule moved to other nights, board services or plans stops
            # closing the ones it leaves, which the listener cannot see any
            # more: they are read here, before they go.
            self._channex_stage_restrictions()
        return super().write(vals)
