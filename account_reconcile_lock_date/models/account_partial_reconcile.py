from odoo import _, models
from odoo.exceptions import UserError

from .account_move import BYPASS_CONTEXT_KEY, BYPASS_GROUP


class AccountPartialReconcile(models.Model):
    _inherit = "account.partial.reconcile"

    def unlink(self):
        self._check_reconcile_lock_date()
        # From inside unlink() the core reverses the cash basis and exchange
        # difference entries it owns, and that loops straight back in here:
        # unlink() -> _reverse_moves(cancel=True) -> remove_move_reconcile() ->
        # unlink(). Those entries cannot be reset to draft by hand either, so
        # leaving them half-reversed would be unrecoverable. Once an unlink is
        # authorised, the cleanup it entails is authorised too. The flag rides
        # on ``self``, which is the recordset the core reads the context from
        # when it searches for the entries to reverse.
        return super(
            AccountPartialReconcile,
            self.with_context(**{BYPASS_CONTEXT_KEY: True}),
        ).unlink()

    def _check_reconcile_lock_date(self):
        """Refuse to break a reconciliation when either of the two entries it
        matches belongs to a locked period.

        Looking at *both* sides is the whole point, and it is what no core
        guard does: the case this exists for is a down payment invoice in a
        closed month matched against a payment in an open one.
        """
        # remove_move_reconcile() runs unconditionally on every button_draft
        # and every _reverse_moves(cancel=True), so an empty recordset is the
        # common case by far and must stay free.
        if not self or self.env.context.get(BYPASS_CONTEXT_KEY):
            return True
        if self.env.user.has_group(BYPASS_GROUP):
            return True
        if all(
            company.reconcile_lock_scope == "disabled" for company in self.company_id
        ):
            return True
        blocked = self.env["account.move"]
        for partial in self:
            moves = partial.debit_move_id.move_id | partial.credit_move_id.move_id
            # Cheapest test first: most partials are nowhere near the lock date.
            locked = moves.filtered(
                lambda m: m.date <= m.company_id._get_reconcile_lock_date()
            )
            if not locked:
                continue
            # Either side being in scope is enough. The counterpart of a down
            # payment invoice is a payment, which is never one itself, so
            # requiring both would mean never firing at all.
            if not any(move._is_reconcile_lock_in_scope() for move in moves):
                continue
            blocked |= locked
        if blocked:
            self._raise_reconcile_lock_date(blocked)
        return True

    def _raise_reconcile_lock_date(self, moves):
        """Build an actionable error naming the entries that block the
        operation. It surfaces in the backend payment widget and, as an HTTP
        400, through both PMS APIs, so it has to read well at a hotel desk."""
        intro = _(
            "You cannot break this reconciliation: it matches accounting "
            "entries that belong to a closed period."
        )
        label = _("Entries in a locked period:")
        lines = [
            _("• %(name)s dated %(date)s (period locked up to %(lock)s)")
            % {
                "name": move.name or _("draft entry"),
                "date": move.date,
                "lock": move.company_id._get_reconcile_lock_date(),
            }
            for move in moves
        ]
        raise UserError("{}\n\n{}\n{}".format(intro, label, "\n".join(lines)))
