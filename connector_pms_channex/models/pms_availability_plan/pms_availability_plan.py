# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import models


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
