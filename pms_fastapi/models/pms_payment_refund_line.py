from odoo import fields, models


class PmsPaymentRefundLine(models.Model):
    """Explicit link between a customer refund and each original payment it
    refunds, with the refunded amount per payment.

    PROVISIONAL / TO REVIEW: this is a first, deliberately simple way to track
    "how much of a payment has already been refunded" (feeding
    ``account.payment.available_refund_amount``) independently of the accounting
    reconciliation, because a refund of an already-reconciled payment is NOT
    reconciled (only a chatter note is left). We want to revisit this later and
    see whether it can be modelled in a better-structured way (e.g. leaning
    entirely on accounting reconciliation, or a dedicated document).
    """

    _name = "pms.payment.refund.line"
    _description = "PMS Payment Refund Line (provisional)"

    refund_payment_id = fields.Many2one(
        comodel_name="account.payment",
        string="Refund",
        required=True,
        ondelete="cascade",
        index=True,
        help="The customerRefund payment created by POST /customer-payments/refunds.",
    )
    origin_payment_id = fields.Many2one(
        comodel_name="account.payment",
        string="Original Payment",
        required=True,
        ondelete="cascade",
        index=True,
        help="The original customer payment being refunded.",
    )
    amount = fields.Monetary(
        string="Refunded Amount",
        currency_field="currency_id",
        help="Amount refunded from the original payment. Always positive.",
    )
    currency_id = fields.Many2one(
        comodel_name="res.currency",
        related="origin_payment_id.currency_id",
        store=True,
        readonly=True,
    )
    is_reconciled = fields.Boolean(
        string="Accounting-reconciled",
        default=False,
        help="True when the refund was reconciled against the original payment "
        "in the receivable account. False when the original payment was already "
        "reconciled and only a chatter note was left for administration.",
    )
