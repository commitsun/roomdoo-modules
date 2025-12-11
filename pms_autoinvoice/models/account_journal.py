from odoo import fields, models


class AccountJournal(models.Model):
    _inherit = "account.journal"

    avoid_autoinvoice_downpayment = fields.Boolean(
        help="Avoid autoinvoice downpayment",
        default=False,
    )
