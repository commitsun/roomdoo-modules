In Odoo, the operations that undo accounting work check no lock date properly.
The standard guards (``_check_fiscalyear_lock_date``,
``_check_tax_lock_date``) resolve the date with
``res.company._get_user_fiscal_lock_date()``, which *replaces* it with
``fiscalyear_lock_date`` for holders of *Accounting / Adviser* instead of
taking the maximum -- advisers, and ``base.user_root``, which crons and queue
jobs run as -- so for them the monthly close is simply invisible. Breaking a
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

Blocking is only half of it: the module also provides the sanctioned way of
doing what used to require breaking a reconciliation. ``_reissue_invoice()``
rectifies a posted invoice that is being replaced without unreconciling
anything. The credit note is issued with ``cancel=False``, the original keeps
the payment it was settled with, and the money is moved onto the replacement
with a balance transfer entry dated today. It only takes that way where
unreconciling does harm: when the invoice, or something reconciled with it,
belongs to a locked period, when it is a down payment invoice, or when
something other than a payment settles it. An invoice of an open period settled
by payments is still reversed the core way, and its replacement takes over the
same payments. The pieces it is made of
(``_keeps_reconciliation_on_rectify()``, ``_rectify_for_replacement()``,
``_rectify_keep_reconciliation()``, ``_settle_rectification()``,
``_create_balance_transfer_entry()``, ``_is_rectified()``) are public, so that
other flows can reuse them -- an API that posts the replacement later, for
instance.

A company opts in by having a transfer journal
(``res.company._get_rectification_transfer_journal()``, empty here). Without
one, ``_reissue_invoice()`` keeps the core behaviour.

The payment of an invoice rectified this way cannot be reset to draft: the
money its credit note took over has already moved on to the replacement, so
reopening the rectified invoice would charge the customer twice. Members of
the bypass group, and code passing the bypass context key, still can.
