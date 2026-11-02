#. Create a direct, on-line sale channel for the booking engine and add the
   pricelists the engine sells to it.
#. On each property to sell online, open the *Booking Engine* tab and set
   that sale channel. A property without it answers 404.
#. Upload the photos in the *Images* tab of each room type, for one property or
   for all of them, and set the size of the rooms.
#. Uncheck *Recommend Rooms in the Booking Engine* to hide the recommended
   rooms of a property.

The module creates the *PMS Booking Engine API* endpoint, run by the
*PMS Booking Engine* user. Its documentation is at
``/booking-engine/v1/docs``.

To take bookings:

#. In the *Booking Engine* tab, list in *Booking Engine Allowed Origins* the
   websites the guest comes back to from the payment gateway, one per line,
   e.g. ``https://www.example.com``. A return URL elsewhere is refused.
#. Set how many minutes the rooms stay held while the guest pays in
   *Booking Engine Hold (minutes)*; 15 by default.
#. Publish the payment providers of the property (*Published*, in test or
   enabled state). A gateway takes part through its own bridge module, e.g.
   *PMS FastAPI Booking Engine - Redsys*.
