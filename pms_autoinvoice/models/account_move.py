from odoo import _, models


class AccountMove(models.Model):
    _inherit = "account.move"

    def _reverse_downpayment_ref(self):
        """Reference carried by the credit note of a down payment.

        The original invoice's own reference is appended when it has one, and
        omitted when it does not -- concatenating it unconditionally is what
        used to raise TypeError on an invoice with no ref.
        """
        self.ensure_one()
        if self.ref:
            return _("Reversal of: %(name)s - %(ref)s", name=self.name, ref=self.ref)
        return _("Reversal of: %(name)s", name=self.name)

    def _reverse_downpayment_invoices(self):
        """Reverse down payment invoices that the final invoice did not absorb.

        Extracted from the three call sites that used to inline it (the
        synchronous sweep of ``autoinvoicing``, ``autovalidate_folio_invoice``
        and ``autoinvoice_folio``) so that there is a single place to override.

        ``cancel=True`` is what makes the core unreconcile the original before
        copying it, which is precisely what a module handling a closed period
        needs to replace: hence the hook.
        """
        if not self:
            return self.browse()
        default_values_list = [
            {"ref": move._reverse_downpayment_ref()} for move in self
        ]
        return self.with_context(sii_refund_type="I")._reverse_moves(
            default_values_list, cancel=True
        )
