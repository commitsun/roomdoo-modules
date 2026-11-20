To create a long-stay reservation, set the reservation type to
``long_stay`` on a PMS reservation and save it. The reservation will be
automatically split into weekly or monthly segments depending on the
configuration of the related room type. The original reservation becomes
the first segment.

Each generated segment has its own check-in and check-out dates and is
linked to the others through a long-stay group.

For each segment, a long-stay service is created automatically:

* It uses the long-stay product configured on the room type.
* The price is computed via the standard PMS service pricing logic.
* The consumption date is the last night of the segment.

The date used for the generated service line depends on the
``long_stay_billing_timing`` field on the PMS Property:

* ``start``: the service line date is the segment check-in date.
* ``end``: the service line date is the last night of the segment
  (checkout minus one day).
