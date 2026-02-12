from odoo import models


class MergePartnerAutomatic(models.TransientModel):
    """
    The idea behind this wizard is to create a list of potential partners to
    merge. We use two objects, the first one is the wizard for the end-user.
    And the second will contain the partner list to merge.
    """

    _inherit = "base.partner.merge.automatic.wizard"

    def action_merge(self):
        self = self.with_context(partner_merge_in_progress=True)
        return super().action_merge()
