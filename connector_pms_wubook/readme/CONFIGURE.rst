**Payments reported by the channel**

Some channels report in the booking how much the guest already paid online
through the channel's own payment gateway (today only the Mirai booking
engine). The connector can register that amount on the folio as a payment.
It is disabled by default and enabled per hotel and channel:

#. Check in the backend type that the channel is mapped to the right agency.
#. Open the Wubook backend of the hotel, tab *Payments*.
#. In *OTA Payment Mapping*, add (or edit) the line of that agency, choose the
   journal and the incoming payment method where the channel settles the
   money, and tick *Register Channel Payments*.

Only one mapping per agency can have it ticked, and it needs an incoming
payment method.

From the activation date, the staff must stop registering by hand the
prepayments of that channel. A folio that already has payments registered by
hand is never paid automatically (a note says so), but a payment registered by
hand after the automatic one would be a duplicate.

Bookings imported before the activation are processed the next time they are
imported (a new version sent by the channel or a manual resynchronization).
