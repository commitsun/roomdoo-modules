Channel extras
~~~~~~~~~~~~~~

Some channels sell extras with the booking (parking, cot, breakfast,
experiences...) and leave them out of the booking amount, so the folio does
not add up to what the guest pays. The connector can add them to the folio as
services. Nothing changes until it is enabled in two places:

#. In the **Backend Type**, **Mappings** tab, **OTAs** list, tick **Import
   Extras** on the channels whose booking amount does not include the extras
   (e.g. the Mirai booking engine, Wubook channel 197). Leave it off for the
   rest: Booking.com, for instance, sends the city tax as an extra, and the
   hotel already charges its own tourist tax.
#. In the **Backend** of each hotel, **Extras** tab, set **Import Extras From**.
   Folios created before that moment never get channel extras: reception added
   them by hand. Tell reception to stop typing the extras of those channels in
   the bookings that arrive from then on.

Each extra is matched by its name with a row of the **Extras** tab of the
backend (also in **Connectors > PMS > Channel Extras**, and for the hotel
managers in **PMS Management > Configuration > Channel Extras**, both showing
the pending rows by default):

* The names a channel has never sent before are added as **pending** rows,
  without product. Assign them the product they are imported as. Names are
  matched without case, accents nor punctuation, but digits count ("Parking 2"
  is not "Parking"). Channels change the names over time (e.g. from Spanish to
  English): several rows can point to the same product.
* Set the action to **Ignore**, or archive the row, for the extras that must
  not reach the folio. Extras the channel lists at 0 as part of a package are
  imported at 0 unless they are ignored.
* The **Fallback Extras Product**, when set, imports the extras of the pending
  rows anyway, named after the extra, so the folio total is right. The folios
  imported that way keep the fallback product when the row gets its own one
  later (**Apply to Pending Folios** does not change them): only the bookings
  imported from then on use it. Without a fallback product, pending extras are
  not imported and a folio note lists them.
* The prices of the channel include taxes, so the products must have taxes
  included in the price; otherwise the extra is not imported and a folio note
  says so.
* Pending extras, and the ones that could not be imported (e.g. the daily
  limit of the product was reached), are retried on every new version of the
  booking. Once fixed, **Apply to Pending Folios** in the row imports them on
  the folios waiting for them, without asking Wubook.

How the extras reach the folio:

* All the extras of a booking go to its first room (by arrival date), even in
  bookings of several rooms: channels do not tell the room of an extra, and
  their quantity may count all the rooms.
* The lines follow the product, as pms places them: per day products consumed
  before or after the night spread their quantity over the nights (from the
  day after the arrival when consumed after the night). The rest get a single
  line: on the arrival date when consumed on check-in, else on the departure
  date, or on the current date of the hotel when the product is not per day and
  the guests are already on board. The total is the one the channel charged.
* When the channel changes or removes an extra, its service is updated or
  deleted, unless reception changed it by hand (product, quantity or total) or
  it is invoiced: then a folio note asks to review it. A service deleted by
  hand is never created again. If the folio already has a service of the same
  product added by hand, the extra is not imported, to avoid charging it
  twice.
* When the channel moves the stay, the services move to the new room.
  Cancelled rooms do not charge their services.
* When the channel splits a booking in several folios (one per part of the
  stay), every folio repeats the extras of the whole booking: they are not
  imported and a folio note asks reception to add them by hand to the right
  folio.
