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

Mirai lines in the folio internal notes
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For the bookings of the Mirai booking engine (Wubook channel 197), the import
can keep on top of the folio internal notes (the "internal notes" shown at the
top of the folio) a few lines for reception, each one starting with
``[Mirai]``. It is disabled by default: enable *Channel Notes* in the row of
the Mirai channel (*Wubook OTA ID* 197) of the *OTAs* list, in the *Mappings*
tab of the backend type. A folio gets its lines with the next import of its
booking. Only Mirai passes such lines for now, so enabling it for other
channels has no effect. The lines are:

* ``[Mirai] Loyalty club member``: Mirai flags the guest as a member of the
  hotel loyalty club. Mirai does not tell reliably whether the guest has just
  joined, so the line only states the membership, and the member id is never
  shown.
* ``[Mirai] Upselling applied: <amount> (according to Mirai, the guest pays it
  at the hotel)``: Mirai applied an upselling. Its amount is already in the
  price of the booking, it is money to collect at the desk. When Wubook splits
  the booking in several folios, the line only goes on the first one.

The lines are written in the language of the hotel and they are rebuilt from
the booking on every import of it: creation, modification, cancellation and
re-import, also when *Import Folios* is run by arrival dates from the backend,
which rewrites them for every Mirai folio of the range. Nothing is written
when they did not change, and the rest of the note is kept untouched and in
its order. As every line starting with ``[Mirai]`` belongs to the import:

* staff notes must go on their own lines, never inside a ``[Mirai]`` line nor
  at the start of a line as ``[Mirai]``: the next import of the booking
  replaces or removes such a line;
* saving the note from a folio opened before a modification of the booking
  was imported can bring back the previous ``[Mirai]`` lines, which then stay
  until the next import of that booking.

Disabling *Channel Notes* stops updating the lines: those already written stay
in the notes until staff removes them.

If the lines cannot be computed, the booking is imported anyway and the error
goes to the server log. When the folio already existed, a note in its messages
also tells reception that its ``[Mirai]`` lines may be outdated, only once: it
is not posted again while the folio has it. Bookings of other channels never
touch the internal notes.

How channel payments are registered
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

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

Room types deriving their price from another one
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A room type can take its price from another room type ("Price Derived From",
in the ``pms`` module): on the days a pricelist has no rule for it, its price
follows the price of its base room type. It has no items on those days, so the
export of the items of the pricelist does not send those prices to Wubook. The
connector pushes them to the standard plans of the regular pricelists with the
``export_derived_prices`` job (``prices`` channel):

* Only for the room types connected to the backend. Connections marked as "No
  Export" (e.g. rooms anchored to another room in Wubook) are left out.
* Only on the days the room type derives its price from a rule of the
  pricelist, never from the list price of a room type, so prices maintained in
  Wubook are not overwritten with list prices. An item of the room type is an
  override: it is exported as any other item, and no derived price is pushed
  on its day, even if the pricing engine does not apply it (e.g. past its sale
  end date).
* With the default board service added, as for the items, rounded to the cent.
* Prices not above 0.01, which Wubook rejects, are left out and logged.

The push is enqueued when an item the derived prices depend on changes (on the
dates of the item and, when it is moved to other dates, room types, properties
or pricelist, on the ones it leaves; removing or moving an override pushes the
derived price back), when the price derivation of a room type changes, when a
room type deriving its price is connected or a plan is created in Wubook, and
on "Export Pricelists" in the backend. Only the backends with some room type
deriving its price connected get it. Without dates, it covers the "Flatten
Forward Window" of the backend, up to the last dated item of the pricelist.
Flatten pricelists already compute every price, the derived ones included.

Importing prices from Wubook does not create items for the room types deriving
their price in the pricelist, which would become overrides; the items they
already have are updated as usual. Removing the derivation of a room type
pushes nothing for it (only for the room types still deriving their price from
it): Wubook keeps its last prices until the room type gets items of its own.
