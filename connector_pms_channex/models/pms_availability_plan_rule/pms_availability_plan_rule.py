# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import models

#: Every field of a rule: any write to one makes the rule the one written last,
#: which is the one that wins where rules overlap.
RESTRICTION_FIELDS = {
    "availability_plan_id",
    "room_type_id",
    "pms_property_id",
    "date_from",
    "date_to",
    "min_stay",
    "min_stay_arrival",
    "max_stay",
    "max_stay_arrival",
    "closed",
    "closed_arrival",
    "closed_departure",
}


class PmsAvailabilityPlanRule(models.Model):
    _inherit = "pms.availability.plan.rule"

    def _channex_stage_restrictions(self):
        """Publish again the nights these rules restrict, on every pricelist of
        their plan and of the plans inheriting from it."""
        for rule in self:
            plans = rule.availability_plan_id._channex_descendants()
            plans.pms_pricelist_ids._channex_stage_restrictions(
                room_type=rule.room_type_id,
                date_from=rule.date_from,
                date_to=rule.date_to,
                pms_properties=rule.pms_property_id,
            )

    def write(self, vals):
        if RESTRICTION_FIELDS & set(vals) and not self.env.context.get(
            "connector_no_export"
        ):
            # A rule moved to other nights, room types or plans stops
            # restricting the ones it leaves, which the listener cannot see
            # any more: they are read here, before they go.
            self._channex_stage_restrictions()
        return super().write(vals)
