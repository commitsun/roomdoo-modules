from datetime import date

from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    reconcile_lock_scope = fields.Selection(
        selection=[
            ("disabled", "Do not block"),
            ("downpayment", "Down payment invoices only"),
            ("all", "Every reconciliation"),
        ],
        string="Reconciliation lock scope",
        default="disabled",
        required=True,
        tracking=True,
        help="Which reconciliations cannot be broken once they involve a locked "
        "period. 'Do not block' disables the guard; 'Down payment invoices only' "
        "guards a reconciliation as soon as either matched entry is a down "
        "payment invoice; 'Every reconciliation' guards them all. The date used "
        "is the company's own accounting lock date.",
    )

    def _get_reconcile_lock_date(self):
        """Lock date enforced when breaking a reconciliation.

        Deliberately NOT ``_get_user_fiscal_lock_date()``. That one *replaces*
        the date with ``fiscalyear_lock_date`` for users holding
        ``account.group_account_manager`` instead of taking the maximum, so the
        month-end close would be invisible to the very people doing the closing;
        and it resolves against the current user, which is uid 1 under the
        ``sudo()`` calls both PMS APIs make.

        Returns ``date.min`` when nothing is locked, mirroring the core so that
        callers can always compare with ``<=``.
        """
        if not self:
            return date.min
        self.ensure_one()
        return max(
            self.period_lock_date or date.min,
            self.fiscalyear_lock_date or date.min,
        )
