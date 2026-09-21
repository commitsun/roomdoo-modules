With a scope configured and a lock date in place, any attempt to undo a
reconciliation involving a locked entry raises an error naming the entries that
block it and the date they are locked up to. The message surfaces in the
backend payment widget and, as an HTTP 400, through any REST layer on top.

What this module does **not** cover, and should not be assumed to:

* **Deleting an** ``account.full.reconcile``. It leaves the partials alive and
  the journal items still flagged as reconciled but without a matching number.
  It does not release the payment, but it is silent corruption, and the
  standard access rights allow it to any invoicing user.
* **Direct** ``write()`` on a partial, or on ``reconciled`` /
  ``amount_residual`` / ``full_reconcile_id`` of a journal item. Those fields
  are not in the core's protected reconciliation fields.
* **Raw SQL**, from psql, the shell or a migration script. Nothing written in
  Python can reach it; the countermeasure there is a periodic audit query for
  inconsistent states, not a guard.
