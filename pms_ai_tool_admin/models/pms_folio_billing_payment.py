from odoo import _, api, models
from odoo.exceptions import UserError

from odoo.addons.pms_ai_tool.tools import guarded_tool, truncate


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    @api.model
    def _ai_billing_folio_payment(self, folio, payment_id):
        """The posted payment of the folio to move, the one asked for or the
        only one."""
        payments = folio.payment_ids.filtered(lambda pay: pay.state == "posted")
        if payment_id:
            payment = payments.filtered(lambda pay: pay.id == payment_id)
            if not payment:
                raise UserError(
                    _(
                        "The payment %(payment)s is not a posted payment of the "
                        "folio %(folio)s."
                    )
                    % {"payment": payment_id, "folio": folio.name}
                )
            return payment
        if not payments:
            raise UserError(_("The folio %s has no posted payments.") % folio.name)
        if len(payments) > 1:
            raise UserError(
                _(
                    "The folio %(folio)s has several payments; choose one with "
                    "payment_id: %(payments)s."
                )
                % {
                    "folio": folio.name,
                    "payments": "; ".join(
                        f"{pay.id} ({pay.date}, {pay.amount} {pay.currency_id.name}, "
                        f"{pay.journal_id.name})"
                        for pay in payments
                    ),
                }
            )
        return payments

    @api.model
    def _ai_billing_check_payment_move(self, payment, source, target):
        if source == target:
            raise UserError(_("The payment is already on the folio %s.") % target.name)
        if payment.partner_type != "customer" or payment.payment_type != "inbound":
            raise UserError(_("Only payments received from customers can be moved."))
        if len(payment.folio_ids) > 1:
            raise UserError(
                _("The payment %s is shared by several folios: it cannot be moved.")
                % payment.name
            )
        receivable = payment.move_id.line_ids.filtered(
            lambda line: line.account_id.account_type == "asset_receivable"
        )
        if receivable.matched_debit_ids or receivable.matched_credit_ids:
            raise UserError(
                _(
                    "The payment %s is already reconciled (for example with an "
                    "invoice): undo the reconciliation in the accounting first."
                )
                % payment.name
            )
        if target.company_id != payment.company_id:
            raise UserError(
                _("The folio %s belongs to another company than the payment.")
                % target.name
            )
        if payment.currency_id not in (source.currency_id & target.currency_id):
            raise UserError(
                _("The payment and the folios must have the same currency.")
            )

    @api.model
    def _ai_billing_payment_amount(self, folio, payment):
        """What the payment counts towards the pending amount of the folio,
        as the folio computes it."""
        lines = folio._get_payment_move_lines(
            payment, self.env["account.bank.statement.line"]
        )
        return folio._get_advance_amount(lines)

    @guarded_tool(
        input_schema={
            "from_folio": {
                "type": "string",
                "maxLength": 40,
                "description": "Code or id of the folio where the payment was "
                "registered by mistake.",
            },
            "to_folio": {
                "type": "string",
                "maxLength": 40,
                "description": "Code or id of the folio the payment belongs to.",
            },
            "payment_id": {
                "type": "integer",
                "description": "Id of the payment to move; needed when the first "
                "folio has several payments (the refusal lists them).",
            },
        },
        output_schema={
            "payment": {"type": "object"},
            "from_folio": {"type": "object"},
            "to_folio": {"type": "object"},
        },
        required_inputs=["from_folio", "to_folio"],
        write=True,
        apply="_apply_pms_folio_payment_move",
        log_result=False,
    )
    def _ai_pms_folio_payment_move(self, from_folio, to_folio, payment_id=None):
        source = self._ai_billing_find_folio(from_folio)
        target = self._ai_billing_find_folio(to_folio)
        payment = self._ai_billing_folio_payment(source, payment_id)
        self._ai_billing_check_payment_move(payment, source, target)
        currency = target.currency_id
        source_after = currency.round(
            source.pending_amount + self._ai_billing_payment_amount(source, payment)
        )
        target_after = currency.round(
            target.pending_amount - self._ai_billing_payment_amount(target, payment)
        )
        warnings = []
        if payment.partner_id and payment.partner_id != target.partner_id:
            warnings.append(
                _("The payer is not the customer of the folio %s.") % target.name
            )
        if currency.compare_amounts(target_after, 0) < 0:
            warnings.append(
                _("The folio %(folio)s will be overpaid by %(amount)s.")
                % {"folio": target.name, "amount": -target_after}
            )

        def folio_row(folio, after):
            return {
                "id": folio.id,
                "code": folio.name,
                "property": folio.pms_property_id.name,
                "amount_total": folio.amount_total,
                "pending_before": folio.pending_amount,
                "pending_after": after,
            }

        return {
            "summary": _(
                "Move the payment %(payment)s of %(amount)s from the folio "
                "%(source)s to the folio %(target)s."
            )
            % {
                "payment": payment.name,
                "amount": payment.amount,
                "source": source.name,
                "target": target.name,
            },
            "definitions": [
                _(
                    "The folio of the payment is changed as in the payment form: "
                    "the payment, its accounting entry and the pending amounts of "
                    "both folios follow. Only posted customer payments of a single "
                    "folio that are not reconciled can be moved."
                ),
            ],
            "warnings": warnings,
            "result": {
                "payment": {
                    "id": payment.id,
                    "name": payment.name,
                    "date": payment.date,
                    "amount": payment.amount,
                    "currency": payment.currency_id.name,
                    "journal": payment.journal_id.name,
                    "payer": payment.partner_id.display_name or None,
                    "reference": truncate(payment.ref),
                },
                "from_folio": folio_row(source, source_after),
                "to_folio": folio_row(target, target_after),
            },
            "plan": {
                "payment_id": payment.id,
                "from_folio_id": source.id,
                "to_folio_id": target.id,
                "from_pending_after": source_after,
                "to_pending_after": target_after,
            },
        }

    def _apply_pms_folio_payment_move(self, plan, **arguments):
        payment = self.env["account.payment"].browse(plan["payment_id"])
        source = self.env["pms.folio"].browse(plan["from_folio_id"])
        target = self.env["pms.folio"].browse(plan["to_folio_id"])
        if payment.folio_ids != source:
            raise UserError(
                _(
                    "The payment changed since the preview; nothing was saved. "
                    "Preview the change again."
                )
            )
        # The same change as editing the folios in the payment form: the
        # payment synchronizes its entry and the folios recompute their amounts.
        payment.write({"folio_ids": [(6, 0, target.ids)]})
        currency = target.currency_id
        if currency.compare_amounts(
            source.pending_amount, plan["from_pending_after"]
        ) or currency.compare_amounts(target.pending_amount, plan["to_pending_after"]):
            raise UserError(
                _(
                    "The pending amounts of the folios do not match the preview; "
                    "nothing was saved. Preview the change again."
                )
            )
        return {
            "summary": _("Payment %(payment)s moved to the folio %(folio)s.")
            % {"payment": payment.name, "folio": target.name},
            "result": {
                "payment_id": payment.id,
                "payment": payment.name,
                "from_folio": {
                    "code": source.name,
                    "pending_amount": source.pending_amount,
                },
                "to_folio": {
                    "code": target.name,
                    "pending_amount": target.pending_amount,
                },
            },
        }
