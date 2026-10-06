Once a company has a lock policy configured, invoices of its reservations can
still be created as drafts (and printed as proforma), but posting them raises an
error while any invoiced reservation matches the condition. When the condition is
no longer met (e.g. after checkout), the same invoice posts normally.

For the built-in policies no domain is needed: pick *Not before check-in* or
*Not before check-out*.

Custom domain examples
~~~~~~~~~~~~~~~~~~~~~~~

With the *Custom domain* policy you can express other rules, e.g.

Do not invoice until the guest has actually checked out (state based)::

    [('state', '!=', 'done')]

Do not invoice unpaid reservations::

    [('folio_payment_state', 'in', ('not_paid', 'partial'))]

Combine conditions (block if not departed **and** not fully paid)::

    ['&', ('checkout', '>', context_today().strftime('%Y-%m-%d')),
          ('folio_payment_state', '!=', 'paid')]
