# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import models


class PmsPaymentCardVault(models.Model):
    _inherit = "pms.payment.card.vault"

    def _before_reset(self):
        """The Wubook card password was sealed with the old key pair."""
        result = super()._before_reset()
        self.env["channel.wubook.backend"].sudo().search(
            [("pms_property_id", "=", self.pms_property_id.id)]
        ).write({"payment_card_password": False})
        return result
