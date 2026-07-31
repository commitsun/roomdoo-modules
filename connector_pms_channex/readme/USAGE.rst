Channels
~~~~~~~~

Channels are the one master this connector does not create. Only Channex knows
which OTAs can actually be connected, and each one has its own mapping screen,
so the hotel connects them in Channex's own UI, embedded in the *Channels* tab
of the backend:

#. **Open Channex** embeds the Channex channels screen. Connect and map the OTAs
   there. Channex has no way of telling Odoo when this is done.
#. **Sync channels** brings in what was connected. Nothing is created on Channex
   from here.
#. Every channel needs an agency. No partner is created automatically: a wrong
   attribution is worse than a missing one, so an unmapped channel is reported
   and left alone. The backend shows how many are still missing.

The agency is shared by every property: a channel code means the same partner in
the whole installation, so it is mapped once.

A channel removed from Channex is deactivated rather than deleted, so bookings
already attributed to it still resolve to their agency.
