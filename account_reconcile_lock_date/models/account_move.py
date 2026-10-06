from odoo import _, models
from odoo.exceptions import UserError

BYPASS_CONTEXT_KEY = "bypass_reconcile_lock_date"
BYPASS_GROUP = "account_reconcile_lock_date.group_bypass_reconcile_lock_date"


class AccountMove(models.Model):
    _inherit = "account.move"

    def _is_reconcile_lock_in_scope(self):
        """Whether this entry is guarded by the lock.

        This is the extension point of the module: override it to narrow or
        widen the scope without touching the guards themselves.
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

    def write(self, vals):
        self._check_reconcile_lock_on_unpost(vals)
        return super().write(vals)

    def _check_reconcile_lock_on_unpost(self, vals):
        """Refuse to reset to draft or cancel an entry of a locked period.

        The core already guards this transition, in the ``write()`` branch
        commented "You can't post subtract a move to a locked period"
        (account/models/account_move.py, the ``'state' in vals and
        move.state == 'posted'`` case). It resolves the date with
        ``_get_user_fiscal_lock_date()`` though, which *replaces* it with
        ``fiscalyear_lock_date`` for holders of ``account.group_account_manager``
        -- and ``base.user_root`` is in that group, so under the ``sudo()``
        calls both PMS APIs make, the monthly close is simply invisible and
        only the fiscal year close applies. This guard closes that gap with the
        module's own date, scope and bypasses.

        Only transitions *away* from ``posted`` are checked. Posting is never
        blocked here: that is a different operation, guarded elsewhere by the
        core, and hardening it would make it impossible to invoice into an open
        period whenever an older one is closed.
        """
        if "state" not in vals:
            return True
        moves = self.filtered(
            lambda move: move.state == "posted" and vals["state"] != "posted"
        )
        if not moves or self.env.context.get(BYPASS_CONTEXT_KEY):
            return True
        # Unposting is common and the guard is off by default: leave before
        # touching groups when no company opted in.
        if all(
            company.reconcile_lock_scope == "disabled" for company in moves.company_id
        ):
            return True
        if self.env.user.has_group(BYPASS_GROUP):
            return True
        blocked = moves.filtered(
            lambda move: move.date <= move.company_id._get_reconcile_lock_date()
            and move._is_reconcile_lock_in_scope()
        )
        if blocked:
            blocked._raise_reconcile_lock_on_unpost()
        return True

    def _raise_reconcile_lock_on_unpost(self):
        """Deliberately not the reconciliation message: there may be no
        reconciliation involved at all here, and talking about one would send
        the front desk looking for the wrong thing."""
        intro = _(
            "You cannot reset to draft or cancel this entry: it belongs to a "
            "closed period."
        )
        label = _("Entries in a locked period:")
        lines = [
            _("• %(name)s dated %(date)s (period locked up to %(lock)s)")
            % {
                "name": move.name or _("draft entry"),
                "date": move.date,
                "lock": move.company_id._get_reconcile_lock_date(),
            }
            for move in self
        ]
        raise UserError("{}\n\n{}\n{}".format(intro, label, "\n".join(lines)))
