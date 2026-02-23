from odoo import fields, models


class AccountPartialReconcile(models.Model):
    _inherit = "account.partial.reconcile"

    credit_move_id = fields.Many2one(index=True)
    debit_move_id = fields.Many2one(index=True)

    def init(self):
        super().init()
        self.env.cr.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_apr_credit_debit
            ON account_partial_reconcile (credit_move_id, debit_move_id)
        """
        )
        self.env.cr.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_apr_debit_credit
            ON account_partial_reconcile (debit_move_id, credit_move_id)
        """
        )
