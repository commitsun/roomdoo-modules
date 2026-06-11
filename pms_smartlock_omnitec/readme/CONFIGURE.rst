The Roomdoo app credentials identify Roomdoo's Omnitec integration (not the
hotel) and live in the deployment environment, never in the database. Set the
pair matching each OsAccess generation in ``.docker/*.env``:

* Modern: ``OMNITEC_CLIENT_ID`` and ``OMNITEC_CLIENT_SECRET``.
* Legacy: ``OMNITEC_LEGACY_CLIENT_ID`` and ``OMNITEC_LEGACY_CLIENT_SECRET``.

Then, on the lock vendor (**PMS ‣ Smart Locks ‣ Vendors**):

#. Set **Type** to *Omnitec / Rent&Pass*.
#. In the **Omnitec Credentials** tab, pick the **OsAccess Version** of the
   hotel's installation and fill the hotel's **Omnitec Username** and
   **Omnitec Password**.

A misconfigured instance (missing environment variable) fails loudly instead
of authenticating with empty credentials.
