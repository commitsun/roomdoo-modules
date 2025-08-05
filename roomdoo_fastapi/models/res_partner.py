from dateutil.relativedelta import relativedelta

from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    total_invoiced_last_year = fields.Monetary(
        compute="_compute_total_invoiced_last_year"
    )

    def _compute_total_invoiced_last_year(self):
        for partner in self:
            today = fields.Date.context_today(self)
            a_year_ago = today - relativedelta(years=1)
            result = self.env["account.move"].read_group(
                domain=[
                    ("partner_id", "child_of", partner.id),
                    ("move_type", "=", "out_invoice"),
                    ("state", "=", "posted"),
                    ("invoice_date", ">", a_year_ago),
                ],
                fields=["amount_total_signed"],
                groupby=[],
            )
            partner.total_invoiced_last_year = (
                result[0]["amount_total_signed"] if result else 0.0
            )
