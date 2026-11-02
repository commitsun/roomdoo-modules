# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import models


class PaymentProvider(models.Model):
    _inherit = "payment.provider"

    def _get_booking_engine_payment_method(self):
        """Payment method of the booking engine (card, bizum) the provider
        charges with; None leaves it out of the engine. Each gateway maps its
        providers in its own bridge module."""
        return None
