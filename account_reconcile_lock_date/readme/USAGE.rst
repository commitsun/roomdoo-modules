With a scope configured and a lock date in place, both guarded operations
raise an error naming the entries that block them and the date they are locked
up to. The message surfaces in the backend and, as an HTTP 400, through any
REST layer on top.

Note that resetting a *reconciled* invoice to draft trips the reconciliation
guard first, because ``button_draft()`` unreconciles before it writes the
state. The second guard is what covers the case the first one cannot see: a
posted entry of a closed period with nothing reconciled against it.

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
