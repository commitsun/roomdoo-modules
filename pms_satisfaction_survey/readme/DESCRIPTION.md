# PMS Satisfaction Survey

Integrates Odoo's native Surveys with the PMS so that hotels can collect guest
feedback automatically after every stay.

Key features:

- Opt-in per `pms.property`.
- Ships a default "Stay Satisfaction Survey" (English + Spanish translation)
  that can be edited freely after installation.
- Configurable timing: send the invitation right at checkout or N hours after.
- When all reservations of a folio reach the `done` state, a tokenized
  `survey.user_input` is created and the invitation email is queued with
  `mail.mail.scheduled_date`, so the standard `mail.ir_cron_mail_scheduler_action`
  cron dispatches it at the right time.
- One survey per folio (strict idempotency): re-checkout does not duplicate.
- `survey.user_input` is extended with `folio_id` and a stored related
  `pms_property_id`, exposed in the list/form/search views so responses can be
  cross-referenced back to the folio and the hotel that produced them.
- Smart button on the folio form to jump straight to the survey response.
