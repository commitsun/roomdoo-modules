Editing a posted invoice from the PMS app (``PATCH /invoices/p/<id>`` of
``pms_api_rest``) replaces it with a new one. ``pms_api_rest`` does it the core
way: a credit note issued with ``cancel=True``, which starts by unreconciling
the original, so the payment that settled it is released. When that payment
belongs to a closed period, the customer balances of a month that is already
closed change.

This module plugs ``account_reconcile_lock_date``'s ``_reissue_invoice()``
into that edit. Where unreconciling does harm -- the invoice or what it is
reconciled with belongs to a locked period, it is a down payment invoice, or
something other than a payment settles it -- the original keeps its payment,
the credit note is issued with ``cancel=False``, and the money the original
collected moves onto the new invoice with a balance transfer entry dated today.
Otherwise it is still the core reversal, and the new invoice takes over the
very payments the original had, not whatever the folio autoreconciliation would
match. Whatever the new invoice still owes then takes the folio's free
payments, as usual.

Odoo installs it by itself when the second of ``pms_api_rest`` and
``account_reconcile_lock_date`` is installed. On a database that already has
both, install it explicitly. A company without a transfer journal keeps the
core behaviour.

Payments follow the same rule (``PATCH /transactions/p/<id>`` and the channel
updates of a folio). A payment of a closed period -- its own date, or that of
something it settles -- is never reset to draft: its reference is written in
place; a change of amount is registered as a payment or a refund for the
difference, dated today and linked to it (the channels see the amount with its
corrections); its customer cannot change when it settles an invoice, whose own
edit moves the money, and moves with a transfer entry otherwise; its journal
cannot change; its date cannot change, except for a payment of an open month
locked only by what it settles, moved in place to another open date. No payment
or refund can be moved or dated into a closed period, which the core would
otherwise do silently. In an open period, an edited payment is reconciled
again with the invoices it settled (``pms_api_rest``). The members of the bypass
group keep the usual behaviour.
