from collections import defaultdict

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models


class AccountPayment(models.Model):
    _inherit = "account.payment"

    @api.model
    def auto_invoice_downpayments(self, offset=0):
        """
        This method is called by a cron job to invoice the downpayments
        based on the company settings.
        """
        date_reference = fields.Date.today() - relativedelta(days=offset)
        payments = self._get_downpayments_to_invoice(date_reference)
        for payment in payments:
            partner_id = (
                payment.partner_id.id or self.env.ref("pms.various_pms_partner").id
            )
            self._create_downpayment_invoice(
                payment=payment,
                partner_id=partner_id,
            )
        return True

    @api.model
    def _get_downpayments_to_invoice(self, date_reference):
        companys = self.env["res.company"].search([])
        payments = self.env["account.payment"]
        for company in companys:
            if company.pms_invoice_downpayment_policy == "all":
                date_ref = fields.Date.today()
            elif company.pms_invoice_downpayment_policy == "checkout_past_month":
                date_ref = fields.Date.today().replace(
                    day=1, month=fields.Date.today().month + 1
                )
            else:
                continue
            payments += self.search(
                [
                    ("state", "=", "posted"),
                    ("partner_type", "=", "customer"),
                    ("company_id", "=", company.id),
                    ("journal_id.avoid_autoinvoice_downpayment", "=", False),
                    ("folio_ids", "!=", False),
                    ("folio_ids.last_checkout", ">=", date_ref),
                    ("date", "<=", date_reference),
                ]
            )
        payments = payments.filtered(lambda p: not p.reconciled_invoice_ids)
        return payments

    @api.model
    def _create_downpayment_invoice(self, payment, partner_id):
        invoice_wizard = self.env["folio.advance.payment.inv"].create(
            {
                "partner_invoice_id": partner_id,
                "advance_payment_method": "fixed",
                "fixed_amount": payment.amount,
            }
        )
        move = invoice_wizard.with_context(
            active_ids=payment.folio_ids.ids,
            return_invoices=True,
        ).create_invoices()
        if payment.payment_type == "outbound":
            move.action_switch_invoice_into_refund_credit_note()
        move.action_post()
        for invoice, payment_move in zip(move, payment.move_id, strict=True):
            group = defaultdict(list)
            for line in (invoice.line_ids + payment_move.line_ids).filtered(
                lambda r: not r.reconciled
            ):
                group[(line.account_id, line.currency_id)].append(line.id)
            for (account, _dummy), line_ids in group.items():
                if (
                    account.reconcile or account.account_type == "liquidity"
                ):  # TODO: liquidity not in account.account_type
                    self.env["account.move.line"].browse(line_ids).reconcile()
        # Set folio sale lines default_invoice_to to partner downpayment invoice
        for folio in payment.folio_ids:
            for sale_line in folio.sale_line_ids.filtered(
                lambda r: not r.default_invoice_to
            ):
                sale_line.default_invoice_to = move.partner_id.id

        return move
