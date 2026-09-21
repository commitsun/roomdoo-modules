from odoo import models


class AccountMove(models.Model):
    _inherit = "account.move"

    def _is_reconcile_lock_in_scope(self):
        """Whether breaking this entry's reconciliations is guarded.

        This is the extension point of the module: override it to narrow or
        widen the scope without touching the guard itself.
        """
        self.ensure_one()
        scope = self.company_id.reconcile_lock_scope
        if scope == "all":
            return True
        if scope == "downpayment":
            # ``_is_downpayment`` is an ``account`` hook that returns False on
            # its own and is overridden by ``sale`` and, on a PMS install, by
            # ``pms``. Without either of them this scope behaves as 'disabled'.
            return self._is_downpayment()
        return False
