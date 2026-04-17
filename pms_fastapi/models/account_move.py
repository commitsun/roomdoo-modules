from odoo import fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    payment_method_ids = fields.Many2many(
        comodel_name="account.payment.method.line",
        string="Payment Methods",
        compute="_compute_payment_method_ids",
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

    def _compute_payment_method_ids(self):
        for move in self:
            receivable_lines = move.line_ids.filtered(
                lambda line: line.account_id.account_type
                in ("asset_receivable", "liability_payable")
            )
            debit_methods = receivable_lines.matched_debit_ids.credit_move_id.payment_id.payment_method_line_id  # noqa: E501
            credit_methods = receivable_lines.matched_credit_ids.debit_move_id.payment_id.payment_method_line_id  # noqa: E501
            move.payment_method_ids = debit_methods | credit_methods

    def _search_payment_method_ids(self, operator, value):
        if operator == "=":
            value = [value]
        elif operator != "in":
            raise NotImplementedError(
                f"Operator '{operator}' is not supported for payment_method_ids search."
            )
        # Use raw SQL to avoid full scans on account.move.line.
        # Start from account.partial.reconcile (much smaller) and join through
        # indexed FK columns to resolve payment method → invoice move directly.
        self.env.cr.execute(
            """
            SELECT DISTINCT aml_inv.move_id
            FROM account_partial_reconcile apr
            JOIN account_move_line aml_pay ON aml_pay.id = apr.credit_move_id
            JOIN account_payment ap ON ap.id = aml_pay.payment_id
            JOIN account_payment_method_line apml ON apml.id = ap.payment_method_line_id
            JOIN account_move_line aml_inv ON aml_inv.id = apr.debit_move_id
            WHERE apml.id = ANY(%s)
            UNION
            SELECT DISTINCT aml_inv.move_id
            FROM account_partial_reconcile apr
            JOIN account_move_line aml_pay ON aml_pay.id = apr.debit_move_id
            JOIN account_payment ap ON ap.id = aml_pay.payment_id
            JOIN account_payment_method_line apml ON apml.id = ap.payment_method_line_id
            JOIN account_move_line aml_inv ON aml_inv.id = apr.credit_move_id
            WHERE apml.id = ANY(%s)
            """,
            (value, value),
        )
        move_ids = [row[0] for row in self.env.cr.fetchall()]
        if not move_ids:
            return [("id", "=", False)]
        return [("id", "in", move_ids)]

    def _compute_min_overdue_date(self):
        for move in self:
            overdue_lines = move.line_ids.filtered("is_overdue")
            move.min_overdue_date = (
                min(overdue_lines.mapped("date_maturity")) if overdue_lines else False
            )

    def _compute_has_overdue_payments(self):
        for move in self:
            move.has_overdue_payments = any(line.is_overdue for line in move.line_ids)

    def _search_has_overdue_payments(self, operator, value):
        if operator != "=":
            raise NotImplementedError(
                "Only '=' operator is supported for has_overdue_payments search."
            )
        if value:
            return [("line_ids.is_overdue", "=", True)]
        return []
