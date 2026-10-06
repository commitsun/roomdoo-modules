TESA Smartair has no cloud and no shared Roomdoo app credentials: Odoo connects
to the hotel's own on-prem server, so everything is configured on the vendor
record, not in the environment.

On the lock vendor (**PMS ‣ Smart Locks ‣ Vendors**):

#. Set **Type** to *TESA Smartair*.
#. In the **TESA Credentials** tab, fill the **TESA Host** (hostname or IP of
   the hotel's Smartair server, without scheme), the **TESA Port** (default
   8181), and the **TESA Operator** name and password.
#. Leave **Verify SSL** off unless the server presents a valid certificate
   (on-prem Smartair servers usually present a self-signed one).
