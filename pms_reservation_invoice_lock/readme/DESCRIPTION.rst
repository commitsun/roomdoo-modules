Blocks the **validation (posting)** of a reservation's regular invoice until a
per-property, configurable condition is met (typically: not before checkout).

The draft can always be created, so a proforma can still be issued; only the
posting is guarded. This prevents receptionists from invoicing a stay too early
while still letting them hand out a proforma on request.

The condition is expressed as an Odoo domain over reservations, so each hotel
can decide when invoicing is allowed without any code change.

Down payment invoices are always allowed, and so is any move that is not a
regular customer invoice.
