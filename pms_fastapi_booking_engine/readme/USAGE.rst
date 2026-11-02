Search a stay of two nights for two adults in one room::

    GET /booking-engine/v1/properties/1/availability?checkIn=2026-11-03&checkOut=2026-11-05&adults=2&children=0&rooms=1

``checkOut`` is exclusive: the last night is the day before. Every parameter is
required.

Each offer is priced for one unit filled with the guests searched up to its
capacity, adults first. The recommended rooms are priced with the guests of
each room. ``availableUnits`` is shared by all the offers of a room type.

To book, the engine sends the offers chosen to
``POST /booking-engine/v1/properties/{propertyId}/bookings/preview``, which
prices them again and tells how the booking is paid, and then the same
selection with the holder to ``POST .../bookings``, with an
``Idempotency-Key`` header per checkout. The creation answers the booking, its
``accessToken`` and the next action: nothing, or a form to submit to the
payment gateway. The rooms are held as pre-reservations until the payment
arrives; the folio is confirmed when the gateway notifies it, and the holds not
paid in time are released by a cron, without penalty.

The gateway sends the guest back to ``returnUrl?bookingId=…&payment=ok|ko``.
That result only guides: the engine reads the status with
``GET /booking-engine/v1/bookings/{bookingId}`` and
``Authorization: Bearer <accessToken>``, and retries a failed payment with
``POST .../bookings/{bookingId}/payments``.

Until the rates have payment policies, the rooms of a rate that cannot be
cancelled for free are paid in full when booking, their tourist tax included,
and the rest at the property.
