This module adds support for long-stay reservations in the PMS.

A new reservation type (``long_stay``) is introduced. When a reservation
of this type is created, it is automatically split into weekly or monthly
segments according to the configuration defined on the room type. The
reservation initially created by the user becomes the first segment, and
the remaining segments are generated automatically.

All segments are linked through a long-stay group, allowing coherent
management of the whole stay while preserving operational independence
of each segment.

For each segment, a corresponding long-stay service line is generated
automatically. The service is based on a dedicated product configured on
the room type, and its price is computed using the standard PMS service
pricing logic, including consumption-date rules. The date used for the
generated service line is configurable on the PMS Property, allowing the
service to be invoiced either at the start of the period or on the last
night of the segment.

The room type form is extended with long-stay configuration fields:

* Period type (weekly or monthly)
* Base period price
* Taxes for the long-stay product

The module integrates cleanly with the PMS pricing architecture and
provides extension hooks to allow other modules to introduce additional
reservation types or pricing rules.
