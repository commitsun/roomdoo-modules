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

The core only applies the fiscal year close to advisers, who are much of the
staff registering payments. This module makes the payments API apply the
company's monthly close to everyone but the members of the bypass group
(``res.company._get_reconcile_lock_date()``): no payment, refund or internal
transfer can be registered or moved into a closed month, nor cancelled there,
and an internal transfer of a closed month cannot be edited.

A payment of a closed period -- its own date, or that of something it settles
-- is never reset to draft either (``account_reconcile_lock_date``'s
``_amend_locked_payment``). Its reference is written in place; a change of
amount is registered as a payment or a refund for the difference, dated today;
its customer cannot change when it settles an invoice (409, the invoice is what
has to change), and moves with a transfer entry otherwise; its date cannot
change, except for a payment of an open month locked only by what it settles,
moved in place to another open date. Cancelling it answers 409: the way out is
a refund. In an open period, an edited payment is reconciled again with the
invoices it settled.

Odoo installs it by itself when the second of ``pms_fastapi`` and
``account_reconcile_lock_date`` is installed. On a database that already has
both, install it explicitly. A company without a transfer journal keeps the
core behaviour.
