Blocks the **validation (posting)** of a reservation's regular invoice until a
per-company, configurable condition is met (typically: not before checkout).

The draft can always be created, so a proforma can still be issued; only the
posting is guarded. This prevents receptionists from invoicing a stay too early
while still letting them hand out a proforma on request.

The condition is chosen per company with a simple policy selector (not before
check-in / not before check-out) and, for advanced cases, a free Odoo domain
over reservations.

Down payment invoices are always allowed, and so is any move that is not a
regular customer invoice.
