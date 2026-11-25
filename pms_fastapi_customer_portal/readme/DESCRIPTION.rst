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
* ``PATCH /portal/{token}/checkin-partners/{checkin_partner_id}``: update a
  guest with merge-patch semantics (``application/merge-patch+json``); answers
  ``204``. A guest that has left or whose state is cancelled answers ``409``
  ``/errors/guest-read-only``.
* ``POST /portal/{token}/checkin-partners/{checkin_partner_id}/document-scan``:
  read an identity document with the document scanning service of the
  property; nothing is saved on the guest.
* ``GET /portal/{token}/countries``, ``GET /portal/{token}/country-states``
  and ``GET /portal/{token}/id-number-categories``: the catalogs the guest
  data refers to by id, with the query parameters and items of the staff
  ``/countries``, ``/country-states`` and ``/id-number-categories`` routes.

Only a confirmed folio can be checked in: a folio that is done or cancelled
answers as an unknown token. A guest that is not in a live reservation of the
folio answers ``404`` with a problem of type ``/errors/guest-not-found``.

Unlike the reception check-in, a guest written from the portal is never linked
to an existing contact found by its document number: a link holder could
otherwise type someone else's document and read that contact's data back.
When the document belongs to an existing contact, the guest keeps the data
sent but is left without a contact, for the reception to link it; otherwise
it gets a new contact as usual.

Each guest may ask for 5 document scans from the portal. Every scan of a
guest that can be modified counts, whether the document is read or not, and
the count is kept on the guest (*Portal document scans*). Once it is reached,
the scan answers ``429`` with a problem of type
``/errors/document-scan-limit`` and the service is not called. The
``ocrAvailable`` flag of the check-in context still tells whether the
property scans documents at all.

Browsers on another origin may send ``GET``, ``PATCH`` and ``POST`` requests,
with any header.
