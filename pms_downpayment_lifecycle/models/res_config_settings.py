from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    downpayment_transfer_journal_id = fields.Many2one(
        related="company_id.downpayment_transfer_journal_id",
        readonly=False,
    )
