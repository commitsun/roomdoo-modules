from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    reconcile_lock_scope = fields.Selection(
        related="company_id.reconcile_lock_scope",
        readonly=False,
    )
