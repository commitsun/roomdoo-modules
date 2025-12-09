from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    self_billed_journal_id = fields.Many2one(
        string="Self billed journal",
        help="Journal used to create self billing",
        comodel_name="account.journal",
        index=True,
        ondelete="restrict",
    )
