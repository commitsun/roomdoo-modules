Open the property form (**PMS ‣ Configuration ‣ Properties**), go to the
**Invoicing** tab and fill **Reservation invoice block domain**.

The field holds an Odoo domain over ``pms.reservation`` describing the
reservations that **cannot be invoiced yet**. When an invoice is posted, the
reservations it invoices are matched against this domain:

* if **any** invoiced reservation matches the domain, posting is blocked (all
  reservations must be outside the domain to allow posting);
* an **empty** domain disables the lock entirely.

Error message
~~~~~~~~~~~~~

Since the domain is free-form, the block error also uses the field **Reservation
invoice block message** (same tab): write there, in plain words, why invoicing is
not allowed yet (e.g. *"Invoices cannot be validated before the guest checks
out"*). If left empty a generic message is used. In both cases the error lists the
reservation codes that are blocking the validation, so reception knows exactly
which stays to look at.

Editing the domain
~~~~~~~~~~~~~~~~~~~

Switch the domain widget to its **code editor** (the ``</>`` icon) to use
date-relative expressions such as ``context_today()`` — the visual editor only
stores fixed literal values, which is not what a daily-reevaluated lock needs.

These helpers are available when the domain is evaluated:

* ``context_today()`` — today's date in the user's timezone
* ``datetime``, ``date``, ``time``, ``timedelta``, ``relativedelta``

Usable fields
~~~~~~~~~~~~~

Only **stored** reservation fields (or fields related to stored ones) can be
used — non-stored computed fields (e.g. ``allowed_checkout``,
``checkin_datetime``) are not searchable and must not be used. The most useful
stored fields are:

* ``checkout`` (Date) — departure date, the main use case
* ``checkin`` (Date) — arrival date
* ``state`` — ``draft``, ``confirm``, ``onboard``, ``done``, ``cancel``,
  ``arrival_delayed``, ``departure_delayed``
* ``reservation_type`` — ``normal``, ``staff``, ``out``
* ``folio_payment_state`` — ``not_paid``, ``partial``, ``paid``,
  ``overpayment``, ``nothing_to_pay``
* ``invoice_status`` — ``no``, ``to_invoice``, ``invoiced``
* ``nights`` (Integer), ``price_total`` (Monetary)
* ``agency_id``, ``sale_channel_origin_id`` (Many2one)

Bypass group
~~~~~~~~~~~~

Users in the group **PMS / Skip reservation invoice lock** can post blocked
invoices. Assign it to the managers allowed to invoice early. Consider
restricting who can edit the property configuration so receptionists cannot
change the domain themselves.
