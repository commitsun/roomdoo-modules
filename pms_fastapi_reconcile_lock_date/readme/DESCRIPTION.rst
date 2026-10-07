Editing a posted invoice through the PMS FastAPI (``PUT /invoices/{id}`` with
``confirmRefund=true``) rectifies it and creates a draft that replaces it.
``pms_fastapi`` does it the core way: it unreconciles the original and issues a
credit note with ``cancel=True``, so the payment that settled it is released.
When that payment belongs to a closed period, the customer balances of a month
that is already closed change.

This module rectifies with ``account_reconcile_lock_date`` instead, where
unreconciling does harm: the invoice or what it is reconciled with belongs to a
locked period, it is a down payment invoice, or something other than a payment
settles it. The original keeps its payment, the credit note is issued with
``cancel=False`` and holds the money the original collected, open, until the
replacement is validated. Then a balance transfer entry dated that day moves
the money onto the replacement, before the folio's free payments are proposed
to it.

Otherwise the original is still reversed the core way, but the payments it
releases are written down on it, and the replacement takes those same payments
over when it is validated, instead of whatever the folio autoreconciliation
would match.

The payments API posts as the superuser, for whom the core only applies the
fiscal year close. This module makes it apply the company's monthly close as
well (``res.company._get_reconcile_lock_date()``): no payment, refund or
internal transfer can be registered or moved into a closed month, nor cancelled
there, and an internal transfer of a closed month cannot be edited.

Odoo installs it by itself when the second of ``pms_fastapi`` and
``account_reconcile_lock_date`` is installed. On a database that already has
both, install it explicitly. A company without a transfer journal keeps the
core behaviour.
