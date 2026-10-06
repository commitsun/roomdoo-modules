Editing a posted invoice from the PMS app (``PATCH /invoices/p/<id>`` of
``pms_api_rest``) replaces it with a new one. ``pms_api_rest`` does it the core
way: a credit note issued with ``cancel=True``, which starts by unreconciling
the original, so the payment that settled it is released. When that payment
belongs to a closed period, the customer balances of a month that is already
closed change.

This module plugs ``account_reconcile_lock_date``'s ``_reissue_invoice()``
into that edit. The original keeps its payment, the credit note is issued with
``cancel=False``, and the money the original collected moves onto the new
invoice with a balance transfer entry dated today. Whatever the new invoice
still owes then takes the folio's free payments, as usual.

Odoo installs it by itself when the second of ``pms_api_rest`` and
``account_reconcile_lock_date`` is installed. On a database that already has
both, install it explicitly. A company without a transfer journal keeps the
core behaviour.
