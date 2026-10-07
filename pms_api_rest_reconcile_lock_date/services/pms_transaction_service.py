from odoo import _
from odoo.exceptions import UserError

from odoo.addons.component.core import Component


class PmsTransactionService(Component):
    _inherit = "pms.transaction.service"

    def _apply_transaction_update(self, transaction, vals):
        """A payment of a locked period (its own date, or that of something it
        settles) is never reset to draft: account_reconcile_lock_date registers
        the change with the date of the day instead, or refuses it. Nor can a
        payment be moved into a locked period: the core would silently move it
        to the first open day instead."""
        if not vals or transaction._reconcile_lock_bypassed():
            return super()._apply_transaction_update(transaction, vals)
        locked = transaction._is_reconcile_locked()
        if not locked and "date" in vals:
            lock_date = transaction.company_id._get_reconcile_lock_date()
            if vals["date"] <= lock_date:
                raise UserError(
                    _(
                        "The new date %(date)s falls within a closed period (locked "
                        "up to %(lock)s). Use a later date.",
                        date=vals["date"],
                        lock=lock_date,
                    )
                )
        if not locked:
            return super()._apply_transaction_update(transaction, vals)
        if "amount" in vals:
            self._ensure_open_cash_session_for(transaction.journal_id)
        transaction._amend_locked_payment(
            vals, correction_vals=self._locked_correction_vals(transaction)
        )
        return transaction

    def _ensure_open_cash_session_for(self, journal):
        """A correction posted on a cash journal needs a cash session, which the
        app opens on its own when a payment is registered."""
        if journal.type != "cash":
            return
        last_session = self._get_last_cash_session(journal_id=journal.id)
        if not last_session or last_session.is_complete:
            self._action_open_cash_session(
                pms_property_id=journal.pms_property_ids[:1].id or False,
                amount=last_session.balance_end_real,
                journal_id=journal.id,
                force=False,
            )

    @staticmethod
    def _locked_correction_vals(payment):
        """The links to the folio that a correction of ``payment`` keeps: they
        are what the folio counts as paid, and what C1 looks at."""
        return {"folio_ids": [(6, 0, payment.folio_ids.ids)]}
