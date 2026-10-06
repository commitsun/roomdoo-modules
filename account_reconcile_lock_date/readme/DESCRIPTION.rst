In Odoo, the operations that undo accounting work check no lock date properly.
The standard guards (``_check_fiscalyear_lock_date``,
``_check_tax_lock_date``) resolve the date with
``res.company._get_user_fiscal_lock_date()``, which *replaces* it with
``fiscalyear_lock_date`` for holders of *Accounting / Adviser* instead of
taking the maximum -- and ``base.user_root`` is one of them, so under any
``sudo()`` call the monthly close is simply invisible. Breaking a
reconciliation is worse still: it checks nothing at all, because it writes no
date and deletes no journal item.

This module adds the missing checks, with a date of its own:

* **Undoing a reconciliation.** It guards
  ``account.partial.reconcile.unlink()``, the single bottleneck every way of
  undoing a reconciliation goes through: the unreconcile button of the payment
  widget, ``remove_move_reconcile()``, the unreconcile wizard, undoing a bank
  statement reconciliation, ``button_draft()`` and
  ``_reverse_moves(cancel=True)``. It looks at **both** entries a partial
  matches, which is what no core guard does: the case this was written for is
  an invoice in a closed month matched against a payment in an open one.

* **Resetting to draft or cancelling a posted entry.** The core already guards
  this transition, but with the permissive date described above, so an adviser
  -- or anything running as the superuser -- walks straight through the
  monthly close.

Reconciling and posting are never blocked. Only undoing them.
