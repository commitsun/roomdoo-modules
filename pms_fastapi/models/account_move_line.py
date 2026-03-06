from odoo import fields, models


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    is_overdue = fields.Boolean(
        string="Is Overdue",
        compute="_compute_is_overdue",
        search="_search_is_overdue",
    )

    def _compute_is_overdue(self):
        today = fields.Date.today()
        for line in self:
            line.is_overdue = (
                line.move_id.state == "posted"
                and line.account_id.account_type
                in ("asset_receivable", "liability_payable")
                and not line.reconciled
                and line.date_maturity
                and line.date_maturity < today
                and line.move_id.payment_state not in ("paid", "invoicing_legacy")
            )

    def _search_is_overdue(self, operator, value):
        if operator != "=":
            raise NotImplementedError(
                "Only '=' operator is supported for is_overdue search."
            )
        today = fields.Date.today()
        return [
            ("move_id.state", "=", "posted"),
            ("date_maturity", "<", today),
            (
                "account_id.account_type",
                "in",
                ["asset_receivable", "liability_payable"],
            ),
            ("reconciled", "=", False),
            ("move_id.payment_state", "not in", ["paid", "invoicing_legacy"]),
        ]
