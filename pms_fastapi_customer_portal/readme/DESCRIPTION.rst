API of the Roomdoo customer portal, the page a guest opens from the link of
their booking.

The link carries the folio access token, and the token is the only credential:
every route reads the folio of that token and nothing else. The API runs as
its own FastAPI app, apart from the staff ``pms_api`` app, with a technical
user that may only run FastAPI endpoints.

Routes:

* ``GET /portal/{token}/folio``: the folio, its customer, the stay and the
  sale lines aggregated for display.
* ``GET /portal/{token}/property``: the property of that folio, with its
  address, location, contact, logo, images and default hours.

A token that matches no folio, or a folio that is still a quotation or has no
reservation, answers ``404`` with an ``application/problem+json`` body of type
``/errors/portal-link-not-found``.
