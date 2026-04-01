The integration runs automatically in the background:

* **Check-In** is triggered when a reservation transitions to *On Board* state.
  The first check-in partner of the reservation is sent to ATV3.
* **Change Data** is triggered when the check-in or check-out date is modified
  on a reservation that is already *On Board*.
* **Change Room** is triggered immediately when today's reservation line room
  is changed. For pre-planned per-night room changes, a daily cron runs at
  12:00 and compares each onboard reservation's current room against the room
  assigned for that day.
* **Check-Out** is triggered when the checkout action is performed on the
  reservation.

If the Televes API is unreachable or returns an error, the PMS operation
completes normally and a note is posted in the reservation chatter to inform
reception staff.
