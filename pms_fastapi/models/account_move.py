from odoo import fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    has_overdue_payments = fields.Boolean(
        string="Has Overdue Payments",
        compute="_compute_has_overdue_payments",
        search="_search_has_overdue_payments",
    )
    min_overdue_date = fields.Date(
        string="Minimum Overdue Payment Date", compute="_compute_min_overdue_date"
    )

    def _compute_min_overdue_date(self):
        for move in self:
            overdue_dates = [
                payment.date_maturity
                for payment in move.line_ids.filtered(
                    lambda line: line.account_id.account_type
                    in ["asset_receivable", "liability_payable"]
                    and not line.reconciled
                )
            ]
            move.min_overdue_date = min(overdue_dates) if overdue_dates else False

    def _compute_has_overdue_payments(self):
        for move in self:
            move.has_overdue_payments = any(
                payment.date_maturity < fields.Date.today()
                for payment in move.line_ids.filtered(
                    lambda line: line.account_id.account_type
                    in ["asset_receivable", "liability_payable"]
                    and not line.reconciled
                    and line.date_maturity
                )
            )

    def _search_has_overdue_payments(self, operator, value):
        if operator != "=":
            raise NotImplementedError(
                "Only '=' operator is supported for has_overdue_payments search."
            )
        today = fields.Date.today()
        domain = []
        if value:
            domain = [("line_ids.date_maturity", "<", today)]
        return domain
