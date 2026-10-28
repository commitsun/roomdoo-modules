import itertools
from datetime import date

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
        Tier 1 – Folio is fully invoiced (no pending lines) and this is the
                  sole unpaid invoice: all payments belong to it (partial
                  reconciliation is handled by Odoo).
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

        # ── Tier 1: folio fully invoiced and sole unpaid invoice ─────────
        # Only reconcile all payments here when the folio has nothing left
        # to invoice. Otherwise pending lines (e.g. tourist tax invoiced at
        # night) may have payments already collected that don't belong to
        # this invoice.
        folio_fully_invoiced = all(
            f.invoice_status in ("invoiced", "no") for f in move.folio_ids
        )
        if not sibling_invoices and folio_fully_invoiced:
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

    def _get_folio_entry_candidates(self, pay_term_lines):
        """Open customer lines of journal entries linked to this invoice's
        folios that are neither a payment nor a bank statement line: the
        reversal on the first day of the year of a balance moved to another
        account at the close, for instance. The outstanding widget leaves them
        out when their customer differs from the invoice's, or is missing."""
        self.ensure_one()
        if len(pay_term_lines.account_id) != 1:
            return self.env["account.move.line"]
        return self.env["account.move.line"].search(
            [
                ("folio_ids", "in", self.folio_ids.ids),
                ("account_id", "=", pay_term_lines.account_id.id),
                ("company_id", "=", self.company_id.id),
                ("parent_state", "=", "posted"),
                ("reconciled", "=", False),
                ("move_id.move_type", "=", "entry"),
                ("payment_id", "=", False),
                ("statement_line_id", "=", False),
                (
                    "balance",
                    "<" if sum(pay_term_lines.mapped("balance")) > 0 else ">",
                    0,
                ),
            ]
        )

    def _align_folio_entry_partner(self, lines, partner):
        """Give the invoice's customer to the journal entry lines about to
        settle it, so that the customer's balance shows the invoice paid. Lines
        of a locked period keep theirs."""
        self.ensure_one()
        company = self.company_id
        lock_date = max(
            company.period_lock_date or date.min,
            company.fiscalyear_lock_date or date.min,
        )
        to_align = lines.filtered(
            lambda line: not line.payment_id
            and line.partner_id != partner
            and line.date > lock_date
        )
        if to_align:
            to_align.write({"partner_id": partner.id})

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
                current_amounts = {
                    vals["move_id"]: vals["amount"]
                    for vals in (to_reconcile_payments_widget_vals or {}).get(
                        "content", []
                    )
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
                to_propose |= move._get_folio_entry_candidates(pay_term_lines)
                if not to_propose:
                    continue
                to_reconcile = move._get_folio_payment_candidates(to_propose)
                if to_reconcile:
                    try:
                        # The new customer of the journal entry lines must not
                        # outlive a reconciliation that fails.
                        with self.env.cr.savepoint():
                            move._align_folio_entry_partner(
                                to_reconcile, pay_term_lines.partner_id[:1]
                            )
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
        # An invoice that replaces a rectified one is settled first with the
        # money of the invoice it replaces (account_reconcile_lock_date's
        # _reissue_invoice); proposing it the folio's other open payments before
        # that would let them take its balance.
        if not self.env.context.get("skip_folio_autoreconcile"):
            self._autoreconcile_folio_payments()
        return res
