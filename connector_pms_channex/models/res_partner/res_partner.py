# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import models

from ..common.ari import stage_ari


class ResPartner(models.Model):
    _inherit = "res.partner"

    def write(self, vals):
        result = super().write(vals)
        if "sale_channel_id" in vals:
            # The sale channel of an agency decides which channel quota its
            # bookings spend, and so how much of a room type it has left.
            for agency in self.filtered("is_agency"):
                stage_ari(self.env, "channel_caps", (None, agency.id, None))
        return result
