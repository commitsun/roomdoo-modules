Go to connector section and create a new channel backend. Fill form and use buttons to synchronize data:

#. Create Backend
#. Import OTA's Info
#. Import Rooms
#. Import Restrictions & Price Plans
#. Import Availiability, Restrictions & Price Values
#. Synch Push URL
#. Import Reservations

** Update PMS settings to use imported plans as default plans

Avaliability, Restrictions and Pricelist are flagged when change to know when need be uploaded.
All other records modifications are handled to be sent to server as it changes.

All changes are pushed every 5 minutes... and request new bookings every minute.

Modifications using calendars are pushed immediately when press "save" button.

**Payments reported by the channel**

When the channel of a folio has *Register Channel Payments* set (see
Configuration), every import of the booking compares the amount the channel
reports as paid online for the whole booking with what the connector already
registered on the folio, and registers the difference as a payment on the
configured journal, dated on the day of the import (hotel time). Notes on the
folio tell the staff what happened:

* Payments only go up. A booking version reporting 0 paid and 0 due carries
  no information (the channel sends it while an online payment is pending, and
  when a paid booking is modified) and changes nothing. When the channel
  reports less than what was registered, nothing is cancelled nor refunded: a
  note asks to review the payments.
* A payment registered automatically is never registered again, even if the
  staff cancels or edits it.
* Online payment pending: a note shows the deadline the channel gave the
  guest, in hotel time. When the guest pays, the channel sends a new version
  of the booking and the payment is registered then. Unpaid bookings end up
  cancelled by the channel.
* Cancellations keep the payments and register no refund: a note asks to
  review the cancellation penalty and the refund. Nothing is ever registered
  on a cancelled folio.
* A booking Wubook splits in several folios reports the paid amount of the
  whole booking in every folio: it is not registered, a note asks to register
  it by hand on the right folio.
* Not registered either, with a note: folios that already had payments
  registered by hand before the first automatic one, amounts in a currency
  different from the folio's, and folios of a channel booking whose payment
  was already registered on another folio.
* The whole amount paid is registered even if it exceeds the folio total
  (e.g. extras not in the folio yet); a note warns about it.
* If the payment cannot be registered (e.g. a misconfigured journal), the
  booking is imported anyway and a note explains why.

Only the charges made through the channel's own payment gateway are reported.
The charges the hotel makes with its own POS terminal on the guaranteed card
(first night, cancellation penalties...) never reach the channel nor Wubook
and must still be registered by hand.

The amounts reported by the channel, the payment deadline and the payments
registered are shown in the Wubook bindings of the folio (*Connectors* tab).
