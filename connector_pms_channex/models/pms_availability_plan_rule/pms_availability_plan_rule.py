# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

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

    @api.constrains(
        "max_stay_arrival", "date_to", "availability_plan_id", "pms_property_id"
    )
    def _check_channex_max_stay_arrival(self):
        """Refuse a max stay on arrival that would reach a pricelist sold
        through Channex: Channex has a single max stay, which the channels
        apply to the whole stay. Not once the rule is over, as then it is not
        published again."""
        today = fields.Date.context_today(self)
        for rule in self.filtered(
            lambda rule: rule.max_stay_arrival and rule.date_to >= today
        ):
            plans = rule.availability_plan_id._channex_descendants()
            sold = plans.pms_pricelist_ids._channex_sold_in()
            pricelists = [
                pricelist
                for pricelist, pms_property in sold
                if pms_property == rule.pms_property_id
            ]
            if not pricelists:
                continue
            raise ValidationError(
                _(
                    '"%(arrival)s" cannot be set in the availability plan '
                    '"%(plan)s": pricelists sold through Channex take their '
                    "restrictions from it (%(pricelists)s), and Channex has a "
                    "single maximum stay, which the channels apply to the "
                    "whole stay. It has nothing like a maximum stay by day of "
                    'arrival.\n\nUse "%(max_stay)s" instead.'
                )
                % {
                    "arrival": self._fields["max_stay_arrival"]._description_string(
                        self.env
                    ),
                    "max_stay": self._fields["max_stay"]._description_string(self.env),
                    "plan": rule.availability_plan_id.display_name,
                    "pricelists": ", ".join(
                        pricelist.display_name for pricelist in pricelists
                    ),
                }
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
