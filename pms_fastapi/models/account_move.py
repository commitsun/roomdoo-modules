from odoo import fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    payment_method_ids = fields.Many2many(
        comodel_name="account.payment.method",
        string="Payment Methods",
        search="_search_payment_method_ids",
    )
    has_overdue_payments = fields.Boolean(
        string="Has Overdue Payments",
        compute="_compute_has_overdue_payments",
        search="_search_has_overdue_payments",
    )
    min_overdue_date = fields.Date(
        string="Minimum Overdue Payment Date", compute="_compute_min_overdue_date"
    )

    def _search_payment_method_ids(self, operator, value):
        if operator == "=":
            value = [value]
        elif operator != "in":
            raise NotImplementedError(
                f"Operator '{operator}' is not supported for payment_method_ids search."
            )
        payment_amls = self.env["account.move.line"].search(
            [("payment_id.payment_method_line_id.payment_method_id", "in", value)]
        )
        if not payment_amls:
            return [("id", "=", False)]
        partials = self.env["account.partial.reconcile"].search(
            [
                "|",
                ("credit_move_id", "in", payment_amls.ids),
                ("debit_move_id", "in", payment_amls.ids),
            ]
        )
        invoice_aml_ids = list(
            (set(partials.debit_move_id.ids) | set(partials.credit_move_id.ids))
            - set(payment_amls.ids)
        )
        return [("line_ids", "in", invoice_aml_ids)]

    def _compute_min_overdue_date(self):
        for move in self:
            overdue_dates = [
                payment.date_maturity
                for payment in move.line_ids.filtered(
                    lambda line: line.account_id.account_type
                    in ["asset_receivable", "liability_payable"]
                    and not line.reconciled
                    and line.date_maturity
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
