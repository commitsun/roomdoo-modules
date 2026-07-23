Open the company form (**Settings ‣ Companies ‣ Companies**, or *Settings ‣
Users & Companies ‣ Companies*) and set **Reservation invoice lock**.

It is a policy selector:

* **Not before check-in** — blocks invoices of reservations whose check-in is
  still in the future (do not invoice stays that have not arrived yet).
* **Not before check-out** — blocks invoices of reservations that have not
  departed yet (the main use case).
* **Custom domain** — shows a free domain field over ``pms.reservation`` for any
  other rule.

Leave it **empty** to disable the lock for the company.

When an invoice is posted, the reservations it invoices are matched against the
resulting condition: if **any** of them matches, posting is blocked (all
reservations must be outside the condition to allow posting).

Custom domain
~~~~~~~~~~~~~

With the **Custom domain** policy, fill **Reservation invoice block domain**.
Switch the domain widget to its **code editor** (the ``</>`` icon) for
date-relative expressions such as ``context_today()`` — the visual editor only
stores fixed literal values. Available helpers: ``context_today()``,
``datetime``, ``date``, ``time``, ``timedelta``, ``relativedelta``.

Only **stored** reservation fields (or fields related to stored ones) can be
used — non-stored computed fields (e.g. ``allowed_checkout``) are not searchable.
Useful stored fields: ``checkout``, ``checkin`` (Date); ``state``,
``reservation_type``, ``folio_payment_state``, ``invoice_status`` (Selection);
``nights`` (Integer), ``price_total`` (Monetary); ``agency_id``,
``sale_channel_origin_id`` (Many2one).

Error message
~~~~~~~~~~~~~

Fill **Reservation invoice block message** (same form, always available) with a
plain-words explanation of why invoicing is not allowed yet (e.g. *"Invoices
cannot be validated before the guest checks out"*). If empty, a generic message
is used. In both cases the error lists the reservation codes that are blocking
the validation, so reception knows exactly which stays to look at.

Bypass group
~~~~~~~~~~~~

Users in the group **PMS / Skip reservation invoice lock** can post blocked
invoices. Assign it to the managers allowed to invoice early.
