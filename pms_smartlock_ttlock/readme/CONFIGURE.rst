The Roomdoo app credentials identify Roomdoo's TTLock integration (shared
across hotels) and live in the deployment environment, never in the database.
Set them in ``.docker/*.env``:

* ``TTLOCK_CLIENT_ID``
* ``TTLOCK_CLIENT_SECRET``

Then, on the lock vendor (**PMS ‣ Smart Locks ‣ Vendors**):

#. Set **Type** to *TTLock*.
#. In the **TTLock Credentials** tab, fill the hotel's **TTLock Username** and
   **TTLock Password**.

A misconfigured instance (missing environment variable) fails loudly instead
of authenticating with empty credentials.
