from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    downpayment_transfer_journal_id = fields.Many2one(
        comodel_name="account.journal",
        string="Down payment transfer journal",
        domain="[('type', '=', 'general')]",
        check_company=True,
        help="Journal used for the entries that move a balance from one customer "
        "onto another: a down payment invoiced to the customer of the final "
        "invoice, or an invoice rectified and re-issued without breaking its "
        "reconciliations. Without it neither is done automatically.",
    )

    def _get_downpayment_transfer_journal(self):
        """No fallback on purpose: the first miscellaneous journal of a company
        is whatever happens to come first (an amortisation or a payroll
        journal), and a transfer posted there is worse than none."""
        self.ensure_one()
        return self.downpayment_transfer_journal_id

    def _get_rectification_transfer_journal(self):
        return (
            super()._get_rectification_transfer_journal()
            or self._get_downpayment_transfer_journal()
        )
