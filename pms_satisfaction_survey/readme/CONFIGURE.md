# Configuration

1. Open **PMS / Configuration / Properties** and edit the property.
2. Go to the **Satisfaction Survey** tab.
3. Toggle **Send satisfaction survey**.
4. Optionally pick a different survey under **Satisfaction survey**. The
   module ships the default "Stay Satisfaction Survey", but any survey of
   your own can be selected.
5. Choose when the email should be queued:
   - **On checkout**: queued immediately when the last reservation in the
     folio is checked out.
   - **Hours after checkout**: queued with a `scheduled_date` equal to
     `checkout time + delay hours`. The standard mail scheduler cron
     (`mail.ir_cron_mail_scheduler_action`, runs every hour by default)
     dispatches it once the time elapses.
6. Save.
