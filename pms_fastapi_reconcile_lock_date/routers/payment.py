from odoo import _, models

from odoo.addons.account_reconcile_lock_date.models.account_payment import (
    LockedPaymentError,
)


class PmsApiPaymentRouterHelper(models.AbstractModel):
    _inherit = "pms_api_payment.payment_router.helper"

    def _get_payment_lock_date(self, company):
        """The monthly close as well, whoever the user: the core only applies
        the fiscal year close to advisers, who are most of the staff. The
        members of the bypass group keep the core date."""
        lock_date = super()._get_payment_lock_date(company)
        if self.env["account.payment"]._reconcile_lock_bypassed():
            return lock_date
        return max(lock_date, company._get_reconcile_lock_date())

    def _ensure_not_locked(self, payments, detail=None):
        """A payment of an open month that settles a document of a closed one
        cannot be cancelled either: resetting it to draft breaks a
        reconciliation of the closed period. The way out is a refund."""
        super()._ensure_not_locked(payments, detail=detail)
        for payment in payments:
            if payment._reconcile_lock_bypassed() or not payment._is_reconcile_locked():
                continue
            self._problem(
                409,
                "/errors/fiscal-lock-date",
                _("Fiscal lock date"),
                detail
                or _(
                    "Payment %s settles a document of a closed period, so it "
                    "cannot be cancelled. Register a refund instead.",
                    payment.name,
                ),
            )

    def _apply_partner_payment_update(self, payment, vals):
        """A payment of a locked period (its own date, or that of something it
        settles) goes through account_reconcile_lock_date: one of an open period
        that settles a document of a closed one is changed and reconciled again,
        one of a closed period is refused with a 409."""
        if payment._reconcile_lock_bypassed() or not payment._is_reconcile_locked():
            return super()._apply_partner_payment_update(payment, vals)
        if "amount" in vals:
            self._ensure_open_cash_session(payment.journal_id)
        try:
            payment._amend_locked_payment(vals)
        except LockedPaymentError as error:
            self._problem(
                409,
                "/errors/locked-payment-%s" % error.code,
                _("Payment of a closed period"),
                str(error),
            )
