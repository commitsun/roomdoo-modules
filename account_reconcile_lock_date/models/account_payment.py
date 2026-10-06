from odoo import _, models
from odoo.exceptions import UserError

from .account_move import BYPASS_CONTEXT_KEY, BYPASS_GROUP


class AccountPayment(models.Model):
    _inherit = "account.payment"

    def action_draft(self):
        self._check_settles_no_rectified_invoice()
        return super().action_draft()

    def _check_settles_no_rectified_invoice(self):
        """Refuse to reset a payment that settles an invoice already rectified.

        An invoice rectified keeping its reconciliations (see
        ``_rectify_keep_reconciliation``) still holds the payment it was
        settled with, while the money its credit note took over has moved on
        to the invoice that replaced it. Resetting the payment unreconciles it,
        so the rectified invoice would owe its amount again with nothing left
        to settle it: the customer would end up charged twice. Whatever the
        period, which is why this is not part of the lock date guards.
        """
        if self.env.context.get(BYPASS_CONTEXT_KEY):
            return True
        for payment in self:
            rectified = payment.reconciled_invoice_ids.filtered(
                lambda move: move.state == "posted"
                and move.move_type in ("out_invoice", "in_invoice")
                and move._is_rectified()
            )
            if not rectified or self.env.user.has_group(BYPASS_GROUP):
                continue
            raise UserError(
                _(
                    "Payment %(payment)s cannot be changed: it settles "
                    "%(invoices)s, which has already been rectified and replaced "
                    "by a new invoice that received its money. Changing the "
                    "payment would make the rectified invoice owe it again. "
                    "Register the difference as a new payment or refund, or ask "
                    "accounting.",
                    payment=payment.display_name,
                    invoices=", ".join(rectified.mapped("display_name")),
                )
            )
        return True
