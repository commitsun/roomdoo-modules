In Odoo, **undoing** a reconciliation checks no lock date at all. The standard
guards (``_check_fiscalyear_lock_date``, ``_check_tax_lock_date``) fire when an
entry's date or state is written, or when journal items are deleted; breaking a
reconciliation does neither, so it goes through untouched. A payment can be
detached from an invoice of a closed, already declared period without anything
raising.

This module adds the missing check. It guards
``account.partial.reconcile.unlink()``, the single bottleneck every way of
undoing a reconciliation goes through: the unreconcile button of the payment
widget, ``remove_move_reconcile()``, the unreconcile wizard, undoing a bank
statement reconciliation, ``button_draft()`` and ``_reverse_moves(cancel=True)``.

The check looks at **both** entries a partial matches, which is what no core
guard does: the case this was written for is an invoice in a closed month
matched against a payment in an open one.

Reconciling is never blocked -- only undoing it.
