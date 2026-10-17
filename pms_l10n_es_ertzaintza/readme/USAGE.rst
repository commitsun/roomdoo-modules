Day to day, nothing has to be done by hand: reservations and traveller
reports are queued automatically and sent by the scheduled actions.

**Where to look**

*PMS > Reservations > Ertzaintza Communications* lists every communication
with its state:

* *Pending notification*: queued, waiting for the next scheduled run.
* *Incomplete guest data*: something is missing (the missing data is listed
  in the *Errors* field) or not every guest has checked in yet. The hourly
  scheduled action retries it and, after 20 hours, reports the guests that
  are on board.
* *Rejected by the Ertzaintza*: the service refused the data. The reason is
  in *Errors* and is also posted in the reservation chatter. Fix the guest
  data and press *Force send*.
* *Error sending*: the service could not be reached or refused the
  certificate. It is retried automatically up to five times.
* *Processed*: accepted. The Ertzaintza request identifier is stored in
  *Request UUID*.

A communication that is re-sent with the same reference answers "the
contract already exists"; that answer is treated as success, so a retry
after a timeout never duplicates a report.

**Scheduled actions**

Enable them in *Settings > Technical > Scheduled Actions* once the
property is configured:

* Ertzaintza Automatic Sending Pending Reservation Communications
* Ertzaintza Automatic Sending Pending Traveller Reports
* Ertzaintza Automatic Sending Incomplete Traveller Reports

**Sending the file by hand**

While the certificate of the establishment is not authorised for the web
service, select the communications in the list, use the *Download
Ertzaintza XML* action and upload the file in the "Envío fichero" tab of
the Ertzaintza portal. Then use *Mark as sent manually* so they are not
sent again.

**Limitations of the service**

The A19 service has no cancellation and no modification operation. When a
reservation is cancelled or changed after being reported, the module only
posts a note in the reservation chatter: the correction has to be done in
the Ertzaintza portal.
