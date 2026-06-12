The Roomdoo app credentials identify Roomdoo's Salto KS integration (not the
hotel) and live in the deployment environment, never in the database. Set them
in ``.docker/*.env``:

* ``SALTO_CLIENT_ID`` and ``SALTO_CLIENT_SECRET``.

Then, on the lock vendor (**PMS ‣ Smart Locks ‣ Vendors**):

#. Set **Type** to *Salto KS*.
#. In the **Salto Credentials** tab, pick the **Salto Environment** and fill the
   hotel's **Salto Username**, **Salto Password** and **Salto Site ID**.
#. Press **Fetch Salto roles** to pull the site's roles, then set **Guest Role**
   to the basic *User* role (it only opens doors). Never assign an admin role to
   guests; the integration account itself must be a site admin.

A misconfigured instance (missing environment variable) fails loudly instead of
authenticating with empty credentials.

The retention cron *Smartlocks: Purge revoked Salto grants (retention)* runs
daily and hard-deletes grants that were revoked more than 15 days ago.
