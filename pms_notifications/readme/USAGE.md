The module ships five ready-to-use notification rules (booking confirmation,
pre-arrival information, digital check-in reminder, checkout reminder and thank
you / review request), each one bound to its own email template.

**All of them are created inactive.** They send messages to your guests, so
nothing goes out until somebody reviews the wording and turns the rule on. To
activate one:

1. Go to *Property Management > Configuration > Notification Rules*.
2. Open the rule, review its template (the default texts are generic) and pick
   the properties it applies to. An empty *Properties* field means every
   property in the database.
3. Tick *Active*.

Scheduled rules are triggered by the *PMS Notifications: Run Scheduled Rules*
cron, and queued messages are delivered by *PMS Notifications: Send Pending
Email Notifications*. Note that a scheduled rule fires for every reservation
already inside its time window, so activating one on a live database may send a
batch of messages straight away.

Every attempt is recorded in *Notification Logs*, with its state and error, so
you can check what was sent and to whom.
