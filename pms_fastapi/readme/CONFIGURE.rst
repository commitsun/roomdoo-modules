* Go to  FastAPI > FastAPI endpoints  and create a new one with the data:
    * Name: PMS API
    * App: PMS API
    * Root path: /pmsApi
    * User: PMS Fastapi
* After the creation push the sync registry button and restart the Odoo server.

Browser access (CORS) is set in Settings > Technical > System Parameters. The
legacy API of ``pms_api_rest`` reads the same parameters:

* ``roomdoo_app_url``: origin of the web app, e.g. ``https://app.example.com``.
  A single origin, never a list: links to the web app are built from it too.
  Its pages may call the API with credentials (cookies). When it is empty,
  ``*`` or ``null``, any origin may call the API, without credentials.
* ``roomdoo_app_extra_origins``: further origins allowed to call the API from a
  browser, for clients served from their own host, e.g.
  ``https://other.example.com``. Each one is trusted as much as the web app,
  credentials included, so list only hosts you trust, served over ``https``.
  Exact origins, written as the browser sends them (``scheme://host[:port]``,
  host in lowercase, no default port, no path, no wildcards): anything else
  never matches. Separated by commas, spaces or line breaks. Ignored when
  ``roomdoo_app_url`` is empty, ``*`` or ``null``.

A change saved on that screen applies without a restart. One written straight
into the database, or from an Odoo shell that does not call
``env.registry.signal_changes()`` after committing, only applies after a
restart. A worker serving several databases may also, rarely, keep the previous
list for this API: restart when a change does not show, and after removing an
origin that must stop working at once. Even then, browsers may reuse a
preflight answer they already have, for up to 24 hours on the legacy API and 10
minutes on this one.
