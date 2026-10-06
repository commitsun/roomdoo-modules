Go to **Settings > Accounting**, section **Invoicing**, and set **Down payment
transfer journal**: the journal the transfer entry is posted in. It is stored
per company, and without it nothing is transferred automatically: the first
miscellaneous journal of a company is whatever happens to come first, and a
transfer posted there is worse than none. ``account_reconcile_lock_date`` uses
the same journal for the invoices it rectifies without unreconciling them.

Nothing else needs configuring. The module acts by itself on every final
invoice that absorbs a down payment issued to another customer, on every refund
of money collected as a down payment, on every cancelled folio that still has
one standing, and on every payment whose amount changes after it was invoiced.

What is pending or needs a decision is listed under **Accounting >
Miscellaneous > Down payments**.
