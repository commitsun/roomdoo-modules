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
