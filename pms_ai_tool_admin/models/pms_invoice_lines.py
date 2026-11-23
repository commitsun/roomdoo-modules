from odoo import _, models
from odoo.exceptions import UserError

from odoo.addons.pms_ai_tool.tools import guarded_tool, truncate

from .common import amount

MAX_MATCHES = 5
LABEL_CHARS = 120
INVOICE_TYPES = ("out_invoice", "out_refund", "in_invoice", "in_refund")


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    def _ai_invoice_link(self, move):
        return {
            "id": move.id,
            "number": move.name,
            "type": move.move_type,
            "state": move.state,
            "date": move.invoice_date or move.date,
            "total": amount(move.amount_total),
        }

    def _ai_invoice_payload(self, move):
        lines = []
        for line in move.invoice_line_ids.filtered(
            lambda ln: ln.display_type == "product"
        ).sorted("sequence"):
            lines.append(
                {
                    "description": truncate(line.name, LABEL_CHARS),
                    "quantity": line.quantity,
                    "untaxed": amount(line.price_subtotal),
                    "tax": amount(line.price_total - line.price_subtotal),
                    "total": amount(line.price_total),
                    "taxes": line.tax_ids.mapped("name"),
                }
            )
        applied = [
            self._ai_settlement_row(item)
            for item in self._ai_move_settlements(move)[move.id]
        ]
        return {
            "id": move.id,
            "number": move.name,
            "type": move.move_type,
            "state": move.state,
            "partner": move.commercial_partner_id.name,
            "date": move.invoice_date or move.date,
            "due_date": move.invoice_date_due,
            "property_id": move.pms_property_id.id or None,
            "property": move.pms_property_id.name or None,
            "currency": move.currency_id.name,
            "untaxed": amount(move.amount_untaxed),
            "tax": amount(move.amount_tax),
            "total": amount(move.amount_total),
            "amount_due": amount(move.amount_residual),
            "payment_state": move.payment_state,
            "is_down_payment": bool(move._is_downpayment()),
            "reverses": self._ai_invoice_link(move.reversed_entry_id)
            if move.reversed_entry_id
            else None,
            "reversed_by": [
                self._ai_invoice_link(credit)
                for credit in move.reversal_move_id.sorted("id")
            ],
            "lines": lines,
            "applied_payments": applied,
        }

    @guarded_tool(
        input_schema={
            "number": {
                "type": "string",
                "maxLength": 64,
                "description": "Number of the invoice or credit note, as printed "
                "(exact match).",
            },
            "move_id": {
                "type": "integer",
                "description": "Id of the invoice or credit note.",
            },
        },
        output_schema={"invoices": {"type": "array", "items": {"type": "object"}}},
        log_result=False,
    )
    def _ai_pms_invoice_lines(self, number=None, move_id=None):
        if not number and not move_id:
            raise UserError(_("Give the number or the id of the invoice."))
        domain = [
            ("move_type", "in", INVOICE_TYPES),
            ("company_id", "in", self._ai_scope_company_ids()),
            "|",
            ("pms_property_id", "=", False),
            ("pms_property_id", "in", self._ai_scope_property_ids()),
        ]
        domain.append(("id", "=", move_id) if move_id else ("name", "=", number))
        moves = self.env["account.move"].search(domain, limit=MAX_MATCHES + 1)
        if not moves:
            raise UserError(_("No invoice or credit note found for that number."))
        warnings = []
        if len(moves) > MAX_MATCHES:
            moves = moves[:MAX_MATCHES]
            warnings.append(
                _("Several documents share that number: only %s are shown.")
                % MAX_MATCHES
            )
        invoices = [self._ai_invoice_payload(move) for move in moves]
        first = invoices[0]
        return {
            "summary": _(
                "%(number)s: %(lines)s lines, total %(total)s, state %(state)s."
            )
            % {
                "number": first["number"],
                "lines": len(first["lines"]),
                "total": first["total"],
                "state": first["state"],
            },
            "period": {},
            "definitions": [
                _(
                    "Lines: the product lines of the document, with the untaxed "
                    "amount, the tax and the total of each one. Section and note "
                    "lines are not listed."
                ),
                _(
                    "reverses: the invoice a credit note was created from. "
                    "reversed_by: the credit notes created from an invoice."
                ),
                _(
                    "applied_payments: the reconciliations of the document, dated "
                    "by the accounting date of the counterpart entry. kind says "
                    "what was applied: a payment, a bank statement line, a down "
                    "payment invoice, a credit note or another entry."
                ),
                _("Sensitive: accounting data."),
            ],
            "warnings": warnings,
            "result": {"invoices": invoices},
        }
