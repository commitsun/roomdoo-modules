from odoo import models


class AccountMove(models.Model):
    _inherit = "account.move"

    def write(self, vals):
        res = super().write(vals)
        # Posting, resetting to draft and cancelling a payment all end here
        if "state" in vals:
            payments = self.payment_id
            if payments:
                self.env["pms.neobookings.payment.sync"]._safe_track_payments(payments)
        return res
