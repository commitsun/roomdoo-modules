from odoo import models


class PmsApiInvoiceRouterHelper(models.AbstractModel):
    _inherit = "pms_api_invoice.invoice_router.helper"

    def _rectify_posted_invoice(self, invoice):
        """Where unreconciling does harm (see account_reconcile_lock_date's
        ``_keeps_reconciliation_on_rectify``), rectify without unreconciling:
        the original keeps its payment, and its credit note holds the money it
        collected, open, until the replacement is posted and takes it over.

        Otherwise it is still the core reversal. The payments it releases are
        written down on the original, so that the replacement, posted later,
        takes those same payments over (see account.move._post)."""
        if not invoice._keeps_reconciliation_on_rectify():
            invoice.rectification_released_line_ids = (
                invoice._rectification_payment_lines()
            )
            return super()._rectify_posted_invoice(invoice)
        return invoice._rectify_for_replacement()
