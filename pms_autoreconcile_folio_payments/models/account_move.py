import itertools

from odoo import _, models
from odoo.tools import float_compare, float_is_zero


class AccountMove(models.Model):
    _inherit = "account.move"

    def _get_folio_payment_candidates(self, to_propose):
        """Return payment lines from *to_propose* that can be deterministically
        matched against this invoice, or an empty recordset when the match is
        ambiguous or impossible.

        The algorithm applies four tiers in order — the first one that produces
        an unambiguous result wins:

        Tier 0 – Early exits (empty data, zero residual, credit notes).
        Tier 1 – This is the sole unpaid invoice in its folio(s): all payments
                  belong to it (partial reconciliation is handled by Odoo).
        Tier 2 – Exactly one payment line matches the invoice residual and no
                  sibling invoice has the same residual (no ambiguity).
        Tier 3 – A unique subset of payment lines sums to the invoice residual
                  and no line in that subset individually matches a sibling's
                  residual.
        Tier 4 – No deterministic match: return empty (manual reconciliation).
        """
        self.ensure_one()
        move = self
        rounding = move.currency_id.rounding
        residual = move.amount_residual
        empty = self.env["account.move.line"]

        # ── Tier 0: early exits ──────────────────────────────────────────
        if not to_propose or float_is_zero(residual, precision_rounding=rounding):
            return empty

        if move.move_type in ("out_refund", "in_refund"):
            return empty

        to_propose = to_propose.filtered(
            lambda ln: not float_is_zero(
                ln.amount_residual, precision_rounding=rounding
            )
        )
        if not to_propose:
            return empty

        # ── Sibling unpaid invoices (shared across tiers 1-3) ────────────
        sibling_invoices = move.folio_ids.mapped("move_ids").filtered(
            lambda m: m.state == "posted"
            and m.payment_state in ("not_paid", "partial")
            and m.is_invoice(include_receipts=True)
            and m.id != move.id
            and m.move_type not in ("out_refund", "in_refund")
        )

        # ── Tier 1: sole unpaid invoice → all payments belong here ───────
        if not sibling_invoices:
            return to_propose

        # ── Tier 2: exact 1:1 match with ambiguity check ────────────────
        exact_matches = to_propose.filtered(
            lambda ln: float_compare(
                abs(ln.amount_residual), residual, precision_rounding=rounding
            )
            == 0
        )
        if len(exact_matches) == 1:
            ambiguous = any(
                float_compare(
                    sib.amount_residual, residual, precision_rounding=rounding
                )
                == 0
                for sib in sibling_invoices
            )
            if not ambiguous:
                return exact_matches

        # ── Tier 3: subset-sum match with ambiguity check ────────────────
        if len(to_propose) <= 20:
            candidates = [(line, abs(line.amount_residual)) for line in to_propose]
            found_subsets = []
            for size in range(2, len(candidates) + 1):
                for combo in itertools.combinations(candidates, size):
                    combo_sum = sum(amt for _, amt in combo)
                    if (
                        float_compare(combo_sum, residual, precision_rounding=rounding)
                        == 0
                    ):
                        found_subsets.append(combo)
                if found_subsets:
                    break  # smallest subset size wins

            if len(found_subsets) == 1:
                subset_lines = empty
                for line, _amt in found_subsets[0]:
                    subset_lines |= line
                # Check that no individual line in the subset also exactly
                # matches a sibling invoice's residual (would be ambiguous).
                sibling_residuals = [s.amount_residual for s in sibling_invoices]
                ambiguous = any(
                    any(
                        float_compare(amt, sib_res, precision_rounding=rounding) == 0
                        for sib_res in sibling_residuals
                    )
                    for _line, amt in found_subsets[0]
                )
                if not ambiguous:
                    return subset_lines

        # ── Tier 4: no deterministic match ───────────────────────────────
        return empty

    def _autoreconcile_folio_payments(self):
        """Reconcile folio payments with this invoice when the match is
        deterministic (see ``_get_folio_payment_candidates``)."""
        for move in self.filtered(lambda m: m.state == "posted"):
            if move.is_invoice(include_receipts=True) and move.folio_ids:
                move.invalidate_recordset(
                    fnames=["invoice_outstanding_credits_debits_widget"]
                )
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
                        and bool(line.payment_id.folio_ids & move.folio_ids)
                    )
                )
                to_reconcile = move._get_folio_payment_candidates(to_propose)
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
