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
one, so an unmapped channel is highlighted and left alone.

The agency is shared by every property: a channel code means the same partner in
the whole installation, so it is mapped once.

A channel removed from Channex is deactivated rather than deleted, so bookings
already attributed to it still resolve to their agency.
