# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, models


class PmsAvailabilityPlan(models.Model):
    _inherit = "pms.availability.plan"

    def _channex_descendants(self):
        """These plans and every one inheriting from them, however far down:
        a rule of a plan is a rule of the ones that inherit it, where they say
        nothing of their own."""
        plans = todo = self
        while todo:
            todo = todo.child_ids - plans
            plans |= todo
        return plans

    def _channex_ancestors(self):
        """These plans and every one they inherit from, however far up."""
        plans = todo = self
        while todo:
            todo = todo.parent_id - plans
            plans |= todo
        return plans

    @api.constrains("parent_id")
    def _check_channex_max_stay_arrival(self):
        """A plan given another parent inherits its rules, and so do the
        pricelists under it."""
        self._channex_descendants().pms_pricelist_ids._channex_check_max_stay_arrival()
