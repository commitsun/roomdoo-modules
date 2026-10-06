Editing a posted invoice through the PMS FastAPI (``PUT /invoices/{id}`` with
``confirmRefund=true``) rectifies it and creates a draft that replaces it.
``pms_fastapi`` does it the core way: it unreconciles the original and issues a
credit note with ``cancel=True``, so the payment that settled it is released.
When that payment belongs to a closed period, the customer balances of a month
that is already closed change.

This module rectifies with ``account_reconcile_lock_date`` instead. The
original keeps its payment, the credit note is issued with ``cancel=False`` and
holds the money the original collected, open, until the replacement is
validated. Then a balance transfer entry dated that day moves the money onto
the replacement, before the folio's free payments are proposed to it.

Odoo installs it by itself when the second of ``pms_fastapi`` and
``account_reconcile_lock_date`` is installed. On a database that already has
both, install it explicitly. A company without a transfer journal keeps the
core behaviour.
