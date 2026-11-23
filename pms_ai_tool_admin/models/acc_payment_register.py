from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.pms_ai_tool.tools import guarded_tool

from .acc_reconcile import OPEN_ACCOUNT_TYPES
from .common import amount as money

MAX_INVOICES = 20
PAYMENT_TYPES = {"out_invoice": "inbound", "in_invoice": "outbound"}


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    @api.model
    def _ai_pay_journal(self, code, company, payment_type):
        """The bank or cash journal of the company with that code."""
        code = code.strip()
        journals = self.env["account.journal"].search(
            [
                ("code", "=ilike", code),
                ("type", "in", ("bank", "cash")),
                ("company_id", "=", company.id),
            ],
            limit=2,
        )
        if len(journals) != 1:
            raise UserError(
                _(
                    "No single bank or cash journal with the code %(code)s in "
                    "the company %(company)s."
                )
                % {"code": code, "company": company.name}
            )
        if "pms_property_ids" in journals._fields and journals.pms_property_ids:
            if not set(journals.pms_property_ids.ids) & set(
                self._ai_scope_property_ids()
            ):
                raise UserError(
                    _("The journal %s is not one of your properties.") % code
                )
        if not journals._get_available_payment_method_lines(payment_type):
            raise UserError(
                _("The journal %s has no payment method for this kind of payment.")
                % code
            )
        return journals

    @api.model
    def _ai_pay_checked(self, invoices, journal, amount_paid):
        """Invoices, journal and amount of the payment, refused unless the
        payment can be registered as asked."""
        self._ai_rec_check_accounting()
        references = list(dict.fromkeys(ref.strip() for ref in invoices))
        moves = self.env["account.move"]
        for reference in references:
            moves |= self._ai_rec_find_move(reference)
        if len(moves) != len(references):
            raise UserError(_("Some invoices are given twice."))
        if set(moves.mapped("move_type")) not in ({"out_invoice"}, {"in_invoice"}):
            raise UserError(
                _(
                    "Give only customer invoices or only vendor bills (no credit "
                    "notes or other entries)."
                )
            )
        if len(moves.commercial_partner_id) > 1:
            raise UserError(_("The invoices belong to different partners."))
        if len(moves.company_id) > 1:
            raise UserError(_("The invoices belong to different companies."))
        if len(moves.currency_id) > 1:
            raise UserError(_("The invoices are in different currencies."))
        paid = moves.filtered(
            lambda move: move.payment_state in ("paid", "in_payment", "reversed")
            or move.currency_id.is_zero(move.amount_residual)
        )
        if paid:
            raise UserError(_("Already paid: %s.") % ", ".join(paid.mapped("name")))
        open_lines = moves.line_ids.filtered(
            lambda line: line.account_id.account_type in OPEN_ACCOUNT_TYPES
            and not line.reconciled
        )
        if len(open_lines.account_id) > 1 or len(moves.partner_bank_id) > 1:
            raise UserError(
                _(
                    "The invoices use different accounts or bank accounts: "
                    "register their payments separately."
                )
            )
        payment_type = PAYMENT_TYPES[moves[0].move_type]
        journal = self._ai_pay_journal(journal, moves.company_id, payment_type)
        currency = moves.currency_id
        total = currency.round(sum(moves.mapped("amount_residual")))
        if amount_paid is None:
            amount_paid = total
        amount_paid = currency.round(amount_paid)
        if currency.compare_amounts(amount_paid, 0) <= 0:
            raise UserError(_("The amount must be greater than zero."))
        if len(moves) > 1 and currency.compare_amounts(amount_paid, total):
            raise UserError(
                _(
                    "Several invoices are paid for their whole open amount "
                    "(%s): a different amount needs a single invoice."
                )
                % money(total)
            )
        if currency.compare_amounts(amount_paid, total) > 0:
            raise UserError(
                _("The amount is greater than the open amount (%s).") % money(total)
            )
        return moves, journal, payment_type, amount_paid

    @guarded_tool(
        input_schema={
            "invoices": {
                "type": "array",
                "items": {"type": "string", "maxLength": 64},
                "minItems": 1,
                "maxItems": MAX_INVOICES,
                "description": "Numbers or numeric ids of posted customer "
                "invoices or vendor bills of the same partner and type.",
            },
            "journal": {
                "type": "string",
                "maxLength": 16,
                "description": "Code of the bank or cash journal of the payment.",
            },
            "payment_date": {
                "type": "string",
                "format": "date",
                "description": "Date of the payment (YYYY-MM-DD). Default: today.",
            },
            "amount": {
                "type": "number",
                "description": "Amount paid. Default: the whole open amount. A "
                "different (smaller) amount only for a single invoice: it stays "
                "partially paid.",
            },
            "memo": {
                "type": "string",
                "maxLength": 120,
                "description": "Memo of the payment. Default: the references "
                "of the invoices.",
            },
        },
        output_schema={
            "payment": {"type": "object"},
            "invoices": {"type": "array", "items": {"type": "object"}},
        },
        required_inputs=["invoices", "journal"],
        write=True,
        apply="_apply_acc_payment_register",
        log_result=False,
    )
    def _ai_acc_payment_register(
        self, invoices, journal, payment_date=None, amount=None, memo=None
    ):
        payment_date = payment_date or fields.Date.context_today(self)
        moves, journal, payment_type, amount_paid = self._ai_pay_checked(
            invoices, journal, amount
        )
        lock_date = moves.company_id._get_user_fiscal_lock_date()
        if payment_date <= lock_date:
            raise UserError(
                _("The payment date must be after the lock date %s.") % lock_date
            )
        currency = moves.currency_id
        partner = moves.commercial_partner_id
        warnings = []
        if payment_date > fields.Date.context_today(self):
            warnings.append(_("The payment date is in the future."))
        rows = []
        to_allocate = amount_paid
        for move in moves:
            paid_now = min(move.amount_residual, to_allocate)
            to_allocate = currency.round(to_allocate - paid_now)
            after = currency.round(move.amount_residual - paid_now)
            rows.append(
                {
                    "move_id": move.id,
                    "number": move.name,
                    "reference": move.ref or None,
                    "date": move.invoice_date,
                    "due_date": move.invoice_date_due,
                    "total": money(move.amount_total),
                    "residual_before": money(move.amount_residual),
                    "paid_now": money(paid_now),
                    "residual_after": money(after),
                    "becomes": "paid" if currency.is_zero(after) else "partially_paid",
                }
            )
        kind = _("collection") if payment_type == "inbound" else _("payment")
        return {
            "summary": _(
                "Register a %(kind)s of %(amount)s %(currency)s from %(partner)s "
                "in the journal %(journal)s on %(date)s for %(count)s invoices."
            )
            % {
                "kind": kind,
                "amount": money(amount_paid),
                "currency": currency.name,
                "partner": partner.name,
                "journal": journal.code,
                "date": payment_date,
                "count": len(moves),
            },
            "definitions": [
                _(
                    "One posted payment is created for all the invoices, as the "
                    "Register Payment button does with Group Payments, and "
                    "reconciled with them. invoices: open amount before and after "
                    "the payment, in the currency of the invoices."
                ),
            ],
            "warnings": warnings,
            "result": {
                "payment": {
                    "payment_type": payment_type,
                    "partner_id": partner.id,
                    "partner": partner.name,
                    "journal": journal.code,
                    "journal_name": journal.name,
                    "journal_type": journal.type,
                    "date": payment_date,
                    "amount": money(amount_paid),
                    "currency": currency.name,
                    "memo": memo,
                },
                "invoices": rows,
            },
            "plan": {
                "move_ids": moves.ids,
                "journal_id": journal.id,
                "amount": money(amount_paid),
                "residual_after": {
                    str(row["move_id"]): row["residual_after"] for row in rows
                },
            },
        }

    def _apply_acc_payment_register(
        self, plan, invoices, journal, payment_date=None, amount=None, memo=None
    ):
        payment_date = payment_date or fields.Date.context_today(self)
        moves, journal, payment_type, amount_paid = self._ai_pay_checked(
            invoices, journal, amount
        )
        if (
            moves.ids != plan["move_ids"]
            or journal.id != plan["journal_id"]
            or moves.currency_id.compare_amounts(amount_paid, plan["amount"])
        ):
            raise UserError(_("The invoices changed since the preview: preview again."))
        values = {
            "journal_id": journal.id,
            "payment_date": payment_date,
            "amount": amount_paid,
            "group_payment": True,
            "payment_difference_handling": "open",
        }
        if memo:
            values["communication"] = memo
        wizard = (
            self.env["account.payment.register"]
            .with_context(active_model="account.move", active_ids=moves.ids)
            .create(values)
        )
        payments = wizard._create_payments()
        moves.invalidate_recordset()
        actual = {str(move.id): money(move.amount_residual) for move in moves}
        if (
            len(payments) != 1
            or moves.currency_id.compare_amounts(payments.amount, plan["amount"])
            or actual != plan["residual_after"]
        ):
            raise UserError(
                _(
                    "Odoo would register a different payment than the preview; "
                    "nothing was changed. Register it in Odoo."
                )
            )
        return {
            "summary": _("Payment %(payment)s registered for %(count)s invoices.")
            % {"payment": payments.name, "count": len(moves)},
            "result": {
                "payments": [
                    {
                        "payment_id": payment.id,
                        "number": payment.name,
                        "state": payment.state,
                        "payment_type": payment.payment_type,
                        "journal": payment.journal_id.code,
                        "date": payment.date,
                        "amount": money(payment.amount),
                        "currency": payment.currency_id.name,
                        "memo": payment.ref or None,
                    }
                    for payment in payments
                ],
                "invoices": [
                    {
                        "move_id": move.id,
                        "number": move.name,
                        "payment_state": move.payment_state,
                        "residual": money(move.amount_residual),
                    }
                    for move in moves
                ],
            },
        }
