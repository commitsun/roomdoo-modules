Channels
~~~~~~~~

Channels are the one master this connector does not create. Only Channex knows
which OTAs can actually be connected, and each one has its own mapping screen,
so the hotel connects them in Channex's own UI, embedded in Odoo.

*Manage channels*, on the backend, opens one screen with two steps:

#. **Connect your channels** embeds the Channex channels screen. Channex has no
   way of telling Odoo when this is done, hence the button to come back.
#. **Say who each channel is** lists what was connected and asks for the agency
   of each one. It synchronises on its own, so there is nothing to press.

The screen opens straight on the second step once there is something to map,
which is what makes it worth coming back to when the hotel connects a new OTA
months later.

No partner is created automatically: a wrong attribution is worse than a missing
one, so a channel left without an agency is highlighted and let be. An agency
that does not exist in Odoo yet is created from the dropdown itself, through the
contact form, since an agency needs an indirect sale channel and a pricelist
available for PMS.

The agency is shared by every property: a channel code means the same partner in
the whole installation, so it is mapped once.

A channel removed from Channex is deactivated rather than deleted, so bookings
already attributed to it still resolve to their agency.

Availability
~~~~~~~~~~~~

The availability of every room type on Channex is published on its own as it
changes: a reservation made, moved or cancelled, a room added or archived, an
inventory rule of the property, or a booking imported from Channex itself. What
is published is what pms says the property can sell of each room type through
any channel, night by night: its free rooms, capped by the inventory declared
for the property as a whole.

Changes are not sent one by one. They are gathered for a few seconds and sent
together, in one call per property, which is how Channex wants to be told and
what keeps the property within its limit of calls.

*Publish all availability*, on the backend, sends every room type for every
night Channex publishes. Channex calls it a full sync: it is meant for setting a
property going or putting it right, and allowed once a day at most, so it is
not something to schedule. Every day the nights that have just entered the
window are published on their own.

Whatever Channex refuses out of a send is listed on the backend, under
*Availability, rates and restrictions*, and is not sent again until something
changes on it.
