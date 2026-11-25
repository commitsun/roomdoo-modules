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

Online check-in
~~~~~~~~~~~~~~~

The check-in routes are the staff check-in routes of ``pms_fastapi``, with
``/portal/{token}`` in place of ``/folios/{folio_id}``: same request and
response bodies and the same problems. They reuse the staff check-in helper,
so the modules that extend it, as a localization does, extend these routes as
well.

* ``GET /portal/{token}/checkin-guests``: the guests grouped by room.
* ``GET /portal/{token}/checkin-context``: the property and folio data the
  check-in wizard needs.
* ``GET /portal/{token}/checkin-partners/{checkin_partner_id}``: the stored
  data of a guest.
* ``GET /portal/{token}/countries``, ``GET /portal/{token}/country-states``
  and ``GET /portal/{token}/id-number-categories``: the catalogs the guest
  data refers to by id, with the query parameters and items of the staff
  ``/countries``, ``/country-states`` and ``/id-number-categories`` routes.

Only a confirmed folio can be checked in: a folio that is done or cancelled
answers as an unknown token. A guest that is not in a live reservation of the
folio answers ``404`` with a problem of type ``/errors/guest-not-found``.
