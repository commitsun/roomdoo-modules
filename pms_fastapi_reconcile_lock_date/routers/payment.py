from odoo import models


class PmsApiPaymentRouterHelper(models.AbstractModel):
    _inherit = "pms_api_payment.payment_router.helper"

    def _get_payment_lock_date(self, company):
        """The monthly close as well. The API posts as the superuser, for whom
        the core only applies the fiscal year close: a month already closed was
        still open to registering, cancelling and moving payments."""
        return max(
            super()._get_payment_lock_date(company),
            company._get_reconcile_lock_date(),
        )
