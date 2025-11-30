Creating a long-stay reservation
--------------------------------

To create a long-stay reservation:

1. Open a PMS reservation.
2. Set the ``reservation_type`` to ``long_stay``.
3. Save the record.

The reservation will be automatically split into weekly or monthly
segments depending on the configuration of the related room type.
The original reservation becomes the first segment.

Each generated segment:

* has its own check-in and check-out dates,
* is operationally independent,
* is linked to the other segments through a long-stay group.

Service lines
-------------

Each segment automatically receives a long-stay service line:

* based on the long-stay product configured in the room type,
* with a quantity of ``1`` per segment,
* priced using the standard PMS service pricing computation
  (``_get_price_unit_line``),
* using the **last night** of the segment as ``consumption_date``.

Service line date
-----------------

The service line date depends on the PMS Property setting
``long_stay_billing_timing``:

* ``start`` → service is dated on the **segment check-in**.
* ``end``   → service is dated on the **last night** (checkout - 1 day).

Room Type configuration
-----------------------

Enable long-stay support directly on the room type:

* Select the period type (weekly or monthly).
* Set the base price for the long-stay period.
* Assign taxes to the long-stay product.

A dedicated long-stay product is created or updated automatically.

