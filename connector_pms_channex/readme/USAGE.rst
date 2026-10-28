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

What an agency has set aside -- the inventory declared for it, or for the sale
channel it belongs to -- is published as availability rules on the Channex
channels mapped to it, and the OTA sells the lowest of the rule and the room
type's availability. A rule is only made where the agency is capped lower than
the property as a whole; elsewhere the availability of the room type already
says it. What is left of a quota is what gets published, so a sale through the
agency changes the rule of that night. Rules the hotel makes by hand on Channex
are never touched: ours carry a marker at the end of their title.

*Synchronise everything*, on the backend, brings Channex in line with Odoo for
the property, all of it: the property itself, every room type it has rooms of,
the rate plans of the pricelists sold and the channels connected on Channex,
and then the availability of every room type and the rates and restrictions of
every rate plan, for every night Channex publishes, in one call each. Last, it
reads the bookings Channex is holding. The property has to be connected first,
from its *Connectors* tab, since that is where the hotel says whether to take
over one already on Channex.

Channex calls it a full sync: it is meant for setting a property going or
putting it right, not as a routine. In production Channex takes it once a day
at most, so the button refuses a second one within a day and says when it was
last done; staging is not held back. Every day the nights that have just
entered the window are published on their own.

Whatever Channex refuses out of a send is listed on the backend, under
*Availability, rates and restrictions*, and is not sent again until something
changes on it.

Rate plans
~~~~~~~~~~

A pricelist is sold through Channex once it is connected: chosen under *Rate
plans* on the backend, or with *Connect to a channel* on the *Connectors* tab
of the pricelist, which is the same. Taking it out of the backend's list is
disconnecting it, and what Channex refuses either way is said on saving.
Channex has nothing that is
a pricelist, so connecting one creates its rate plans there: one for each board
service the pricelist sells in the property and each room type on Channex,
titled after the pricelist and the board service in the language of the hotel.
They are priced per guest, for each number of adults the room type holds, and
the occupancy marked as primary is the one the room type is priced for in pms.
That one cannot be changed afterwards, as Channex does not allow it.

What each board service includes to eat is told to Channex through its *Meal
plan on Channex*, on the board service. Room Only comes set; a board service
left without one tells nothing.

The rate plans follow the pricelist on their own: renaming it, changing what it
sells, or adding a room type to Channex. A rate plan the hotel already made by
hand on Channex under the same name and room type is taken over, together with
the channels mapped to it; the rest of the hotel's own rate plans are never
touched.

A rate plan no longer sold -- a board service taken out of the pricelist, the
pricelist archived or no longer connected -- is taken down on Channex, each on
its own job. Channex refuses to remove one that a channel is mapped to, and it
is not forced: the job is left failed, saying so, and running it again once the
rate plan is unmapped on Channex takes it down.

Rates and restrictions
~~~~~~~~~~~~~~~~~~~~~~

Every rate plan is published night by night as pms prices and restricts it, on
its own as anything changes: a pricelist item, of the pricelist or of the one
it is priced from; the occupancy rules; the occupancy a room type is priced
for; the rules of the availability plan or of the plans it inherits from; a
board service closed. Like availability, changes are gathered for a few seconds
and sent in one call per property.

Each night gets a price for every number of adults the room type holds: the
room for that many adults under the pricelist, plus the board service for each
of them, tax included as pms prices it. A night that comes out at no price is
closed, since Channex takes no price of 0.

The restrictions are the ones of the pricelist's availability plan: minimum
stay on arrival and through the stay, closed to arrival or departure, and
closed altogether, as is a night the plan closes the board service on. Channex
has a single maximum stay, so the stricter of the two pms has is published. A
night with no rule is published unrestricted.

Children are charged with the rate plan's children fee, which is the one of
the pricelist's occupancy rule for the room type.
