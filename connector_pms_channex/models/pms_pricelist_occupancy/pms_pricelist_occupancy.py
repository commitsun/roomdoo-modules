# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import models

#: Fields of an occupancy rule that move the price of each number of adults,
#: or which prices it moves.
PRICE_FIELDS = {
    "increase_mode",
    "increase_value",
    "decrease_mode",
    "decrease_value",
    "pricelist_id",
    "room_type_id",
}


class PmsPricelistOccupancy(models.Model):
    _inherit = "pms.pricelist.occupancy"

    def _channex_stage_restrictions(self):
        """Publish again the prices these rules derive, on their pricelists
        and on the ones priced from them, which derive from these prices."""
        for rule in self:
            rule.pricelist_id._channex_dependent_pricelists()._channex_stage_restrictions(
                room_type=rule.room_type_id
            )

    def write(self, vals):
        if {"pricelist_id", "room_type_id"} & set(vals) and not self.env.context.get(
            "connector_no_export"
        ):
            # Moved to another pricelist or room type, the rule stops deriving
            # the prices it leaves, which the listener cannot see any more.
            self._channex_stage_restrictions()
        return super().write(vals)
