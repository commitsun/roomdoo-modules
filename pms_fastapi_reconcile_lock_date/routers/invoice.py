from odoo import models


class PmsApiInvoiceRouterHelper(models.AbstractModel):
    _inherit = "pms_api_invoice.invoice_router.helper"

    def _rectify_posted_invoice(self, invoice):
        """Rectify without unreconciling: the original keeps its payment, and
        its credit note holds the money it collected, open, until the
        replacement is posted and takes it over (see account.move._post). With
        nothing reconciled, or no transfer journal, it is still the core
        reversal."""
        if not invoice._keeps_reconciliation_on_rectify():
            return super()._rectify_posted_invoice(invoice)
        return invoice._rectify_for_replacement()
