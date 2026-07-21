Once a property has a block domain configured, invoices of its reservations can
still be created as drafts (and printed as proforma), but posting them raises an
error while any invoiced reservation matches the domain. When the condition is
no longer met (e.g. after checkout), the same invoice posts normally.

Common domains
~~~~~~~~~~~~~~

Do not invoice before checkout (main case)::

    [('checkout', '>', context_today().strftime('%Y-%m-%d'))]

Do not invoice until the guest has actually checked out (state based)::

    [('state', '!=', 'done')]

Do not invoice before checkin::

    [('checkin', '>', context_today().strftime('%Y-%m-%d'))]

Do not invoice unpaid reservations::

    [('folio_payment_state', 'in', ('not_paid', 'partial'))]

Combine conditions (block if not departed **and** not fully paid)::

    ['&', ('checkout', '>', context_today().strftime('%Y-%m-%d')),
          ('folio_payment_state', '!=', 'paid')]
