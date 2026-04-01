Integration between the PMS Roomdoo and the Televes/Arantia ATV3 IPTV system.

Sends PMS events to the Televes PMS Adapter REST API so that hotel room
televisions can display personalised guest information.

Features:

* **Check-In**: notifies ATV3 when a guest checks in, sending room number,
  reservation code, guest name, language and stay dates.
* **Change Data**: automatically notifies ATV3 when guest data or stay dates
  are modified on an active reservation.
* **Change Room**: detects room changes both immediately (when today's
  reservation line is edited) and via a daily cron at noon (for pre-planned
  per-night room assignments).
* **Check-Out**: notifies ATV3 when a guest checks out.

Each property can have its own ATV3 instance configuration. API errors are
logged silently and a note is posted in the reservation chatter so reception
staff are informed without blocking hotel operations.
