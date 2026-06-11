#. Install a vendor connector module (e.g. *PMS Smartlock Omnitec* or
   *PMS Smartlock TTLock*) and configure its credentials as documented there.
#. Go to **PMS ‣ Smart Locks ‣ Vendors** and create a vendor for the property:
   pick its **Type**, fill the connector's credentials tab, and adjust the
   **Confirm Key** if it differs from the vendor default (the key the guest
   presses on the keypad after the PIN, e.g. ``#``).
#. Go to **PMS ‣ Rooms**, open each room and, in the **Smart Lock** tab, set
   its **Lock Vendor** and **Lock Device ID** and assign the common doors the
   room shares with its guests.
#. Open the property form and, in the **Smart Locks** section, register the
   property's shared/common doors (entrance, garage, pool…).
#. Grant the **Smartlock: Administrator** group to the technical users that
   need full manual control over codes. Regular PMS users cannot mutate codes;
   they only reveal PINs.
