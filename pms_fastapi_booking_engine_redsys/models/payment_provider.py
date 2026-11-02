# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import models

# Redsys payment methods (Ds_Merchant_Paymethods) and the method of the engine
REDSYS_PAYMENT_METHODS = {"T": "card", "C": "card", "z": "bizum"}


class PaymentProvider(models.Model):
    _inherit = "payment.provider"

    def _get_booking_engine_payment_method(self):
        if self.code == "redsys":
            return REDSYS_PAYMENT_METHODS.get(self.redsys_pay_method or "T")
        return super()._get_booking_engine_payment_method()
