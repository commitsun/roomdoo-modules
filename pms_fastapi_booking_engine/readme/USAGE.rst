Search a stay of two nights for two adults in one room::

    GET /booking-engine/v1/properties/1/availability?checkIn=2026-11-03&checkOut=2026-11-05&adults=2&children=0&rooms=1

``checkOut`` is exclusive: the last night is the day before. Every parameter is
required.

Each offer is priced for one unit filled with the guests searched up to its
capacity, adults first. The recommended rooms are priced with the guests of
each room. ``availableUnits`` is shared by all the offers of a room type.
