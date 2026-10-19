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
