from odoo import fields, models


class AccountPaymentMethodLine(models.Model):
    _inherit = "account.payment.method.line"

    payment_method_id = fields.Many2one(index=True)
