from odoo import _, models


class AccountMove(models.Model):
    _inherit = "account.move"

    def _autoreconcile_folio_payments(self):
        """
        Reconcile payments with the invoice
        """
        for move in self.filtered(lambda m: m.state == "posted"):
            if move.is_invoice(include_receipts=True) and move.folio_ids:
                to_reconcile_payments_widget_vals = (
                    move.invoice_outstanding_credits_debits_widget
                )
                if not to_reconcile_payments_widget_vals:
                    continue
                current_amounts = {
                    vals["move_id"]: vals["amount"]
                    for vals in to_reconcile_payments_widget_vals["content"]
                }
                pay_term_lines = move.line_ids.filtered(
                    lambda line: line.account_type
                    in ("asset_receivable", "liability_payable")
                )
                to_propose = (
                    self.env["account.move"]
                    .browse(list(current_amounts.keys()))
                    .line_ids.filtered(
                        lambda line,
                        pay_term_lines=pay_term_lines,
                        move=move: line.account_id == pay_term_lines.account_id
                        and line.payment_id.folio_ids in move.folio_ids
                    )
                )
                to_reconcile = to_propose.filtered(
                    lambda line, move=move: abs(line.balance) == move.amount_residual
                )
                if to_reconcile:
                    try:
                        (pay_term_lines + to_reconcile).reconcile()
                    except Exception as e:
                        message = _(
                            """
                            An error occurred while reconciling
                            the invoice with the payments: %s
                            """
                        ) % str(e)
                        move.message_post(body=message)
        return True

    def _post(self, soft=True):
        res = super()._post(soft)
        self._autoreconcile_folio_payments()
        return res
