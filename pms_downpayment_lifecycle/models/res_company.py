from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    downpayment_transfer_journal_id = fields.Many2one(
        comodel_name="account.journal",
        string="Down payment transfer journal",
        domain="[('type', '=', 'general')]",
        check_company=True,
        help="Journal used for the entry that moves a down payment balance from "
        "the customer it was invoiced to onto the customer of the final "
        "invoice. Falls back to the first miscellaneous journal of the company.",
    )

    def _get_downpayment_transfer_journal(self):
        self.ensure_one()
        if self.downpayment_transfer_journal_id:
            return self.downpayment_transfer_journal_id
        return self.env["account.journal"].search(
            [("type", "=", "general"), ("company_id", "=", self.id)], limit=1
        )
