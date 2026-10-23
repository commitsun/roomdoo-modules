Public API of the web booking engine, served as its own FastAPI app under
``/booking-engine/v1``. It needs no token: the engine calls it from the browser
of the hotel website.

``GET /properties/{propertyId}/availability`` returns what the room selection
page shows for a stay: the room types free for every night, the rates
(pricelists) with their cancellation policy, one offer per rate and board, and
the recommended rooms for the guests searched.

* **Availability** counts the rooms free the whole stay, not night by night,
  and each rate applies the restrictions and limits of its availability plan
  (closed days, minimum and maximum stay, quota, maximum availability).
* **Prices** are what the reservation would bill, taxes included: the room
  price of each night, the board services per guest and the tourist tax. A
  night with no price set in the pricelist makes the offer unavailable, instead
  of selling the product list price.
* **Cancellation** comes from the cancellation rule of the pricelist: free
  until the end of the deadline day in the property time zone, or non
  refundable when that day has already passed.
* **Content**: the photos of a room type are its images for the property (its
  own ones and the common ones), as public URLs; its size is the smallest one of
  its rooms there.
* **Recommended** is the cheapest way to lodge every guest in the rooms
  searched, with the least spare capacity.

Request errors (invalid search, missing or malformed parameters, unknown
property or route) are RFC 9457 ``application/problem+json`` responses.
