from odoo.addons.component.core import Component


class PmsInvoiceService(Component):
    _inherit = "pms.invoice.service"

    def _replace_posted_invoice(self, invoice, new_invoice, default_values_list):
        """Rectify without unreconciling: the original keeps its payment, and
        the money it collected reaches the new invoice through the credit note
        and a transfer entry (account_reconcile_lock_date). With nothing
        reconciled, or no transfer journal, it is still the core reversal."""
        invoice.with_context(
            sii_refund_type="I", supplier_invoice_number=invoice.name
        )._reissue_invoice(new_invoice.sudo(), default_values_list)
        # Whatever the new invoice still owes can take the folio's free
        # payments, now that the original's money has reached it.
        new_invoice._autoreconcile_folio_payments()
