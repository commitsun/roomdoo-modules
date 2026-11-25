Create a FastAPI endpoint (*Settings > Technical > FastAPI > FastAPI
Endpoints*) with:

* **App**: Customer Portal API
* **Root path**: the path the portal front calls, e.g. ``/customerPortalApi``
* **User**: Customer portal API

Then sync its registry.

To accept browser calls only from the portal front, set the system parameter
``roomdoo_customer_portal_url`` to its origin, e.g.
``https://portal.example.com``. When it is not set, any origin may call the
API; the portal sends no credentials.
