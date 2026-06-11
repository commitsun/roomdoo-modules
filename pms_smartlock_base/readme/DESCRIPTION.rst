Base module for integrating smart locks with the PMS Roomdoo.

It is provider-agnostic: it models the hotel-side concepts and delegates the
actual lock communication to a vendor-specific connector module (e.g.
*PMS Smartlock Omnitec* or *PMS Smartlock TTLock*). Installed on its own it
has no usable vendor.

Features:

* **Lock vendors**: per-property vendor configuration (``lock.vendor``)
  holding the hotel account and the keypad confirm key.
* **Access credentials**: each reservation produces ``lock.code`` records with
  a PIN the guest types on the keypad. One credential covers the guest's room
  lock plus the common doors that room shares, all under the same PIN.
* **Common / shared locks**: model the property's shared doors (entrance,
  garage, pool…) and assign them to the rooms that grant them to their guests.
* **Audited PIN reveal**: the PIN is hidden from every user (admins included);
  reception reveals it through an action that records each access in an audit
  log.
* **Automation**: a cron grants codes for stays checking in within the next
  24h, reservation changes propagate to the locks, and a cron purges old PIN
  access logs. Vendor sync runs through ``queue_job`` with retries.
