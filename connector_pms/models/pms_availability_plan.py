# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import models


class PmsAvailabilityPlan(models.Model):
    """Carries the generic manual-connect surface, so it is declared once here
    instead of once per channel manager."""

    _name = "pms.availability.plan"
    _inherit = ["pms.availability.plan", "channel.connect.mixin"]
