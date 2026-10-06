from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    reservation_invoice_block_policy = fields.Selection(
        related="company_id.reservation_invoice_block_policy",
        readonly=False,
    )
    reservation_invoice_block_domain = fields.Char(
        related="company_id.reservation_invoice_block_domain",
        readonly=False,
    )
    reservation_invoice_block_message = fields.Text(
        related="company_id.reservation_invoice_block_message",
        readonly=False,
    )
