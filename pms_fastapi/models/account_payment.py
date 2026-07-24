from odoo import api, fields, models


class AccountPayment(models.Model):
    _inherit = "account.payment"

    # See pms.payment.refund.line: provisional refund-tracking, to review.
    refund_breakdown_ids = fields.One2many(
        comodel_name="pms.payment.refund.line",
        inverse_name="refund_payment_id",
        string="Refund Breakdown",
        help="Per-payment breakdown of the payments this refund covers.",
    )
    origin_refund_line_ids = fields.One2many(
        comodel_name="pms.payment.refund.line",
        inverse_name="origin_payment_id",
        string="Refunds Against This Payment",
        help="Refund lines that consume part of this payment.",
    )
    available_refund_amount = fields.Monetary(
        string="Available to Refund",
        currency_field="currency_id",
        compute="_compute_available_refund_amount",
        help="Remaining amount available to refund: original amount minus "
        "previous refunds. Always positive or 0. 0 for anything that is not a "
        "customer payment.",
    )

    @api.depends(
        "amount",
        "pms_api_transaction_type",
        "origin_refund_line_ids.amount",
    )
    def _compute_available_refund_amount(self):
        for payment in self:
            if payment.pms_api_transaction_type != "customer_inbound":
                payment.available_refund_amount = 0.0
                continue
            refunded = sum(payment.origin_refund_line_ids.mapped("amount"))
            currency = payment.currency_id or payment.company_id.currency_id
            available = payment.amount - refunded
            payment.available_refund_amount = max(0.0, currency.round(available))
