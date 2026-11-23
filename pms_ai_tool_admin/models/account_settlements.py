from odoo import models

from .common import amount

SETTLEMENT_ACCOUNT_TYPES = ("asset_receivable", "liability_payable")
INVOICE_TYPES = ("out_invoice", "out_refund", "in_invoice", "in_refund")


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    def _ai_move_settlements(self, moves, cutoff=None):
        """Reconciliation partials of the receivable/payable lines of ``moves``.

        Returns ``{move_id: [settlement, ...]}`` ordered by date. The date of a
        settlement is the accounting date of the counterpart entry (the payment
        or the credit note), not the creation date of the reconciliation. With
        ``cutoff`` only the settlements dated on or before it are kept.
        """
        result = {move.id: [] for move in moves}
        for move in moves:
            lines = move.line_ids.filtered(
                lambda line: line.account_id.account_type in SETTLEMENT_ACCOUNT_TYPES
            )
            for line in lines:
                for partial in line.matched_debit_ids | line.matched_credit_ids:
                    counterpart = (
                        partial.debit_move_id
                        if partial.credit_move_id == line
                        else partial.credit_move_id
                    )
                    if cutoff and counterpart.date > cutoff:
                        continue
                    result[move.id].append(
                        {
                            "partial": partial,
                            "date": counterpart.date,
                            "amount": partial.amount,
                            "counterpart": counterpart.move_id,
                        }
                    )
        for items in result.values():
            items.sort(key=lambda item: (item["date"], item["partial"].id))
        return result

    def _ai_settlement_kind(self, counterpart):
        """What settled a bill: payment, bank statement line, invoice..."""
        if counterpart.payment_id:
            return "payment"
        if counterpart.statement_line_id:
            return "bank_statement"
        if counterpart.move_type in INVOICE_TYPES:
            return (
                "credit_note"
                if counterpart.move_type.endswith("refund")
                else (
                    "down_payment_invoice"
                    if counterpart._is_downpayment()
                    else "invoice"
                )
            )
        return "other_entry"

    def _ai_settlement_row(self, item):
        counterpart = item["counterpart"]
        kind = self._ai_settlement_kind(counterpart)
        payment = counterpart.payment_id
        journal = payment.journal_id or counterpart.journal_id
        row = {
            "date": item["date"],
            "amount": amount(item["amount"]),
            "kind": kind,
            "entry_id": counterpart.id,
            "entry": counterpart.name,
            "journal": journal.name,
            "journal_type": journal.type,
        }
        if payment:
            row["payment_id"] = payment.id
            row["payment_method"] = payment.payment_method_line_id.name
        return row

    def _ai_bill_scope_domain(self, property_ids=None):
        """Companies and properties of the user for account.move searches."""
        domain = [("company_id", "in", self._ai_scope_company_ids())]
        if property_ids:
            domain.append(
                ("pms_property_id", "in", self._ai_scope_property_ids(property_ids))
            )
        else:
            domain += [
                "|",
                ("pms_property_id", "=", False),
                ("pms_property_id", "in", self._ai_scope_property_ids()),
            ]
        return domain
