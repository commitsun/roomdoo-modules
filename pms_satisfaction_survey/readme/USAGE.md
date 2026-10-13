# Usage

Once configured, the workflow is fully automatic:

1. Reservations belonging to a folio are progressively checked out.
2. When the last active (non-cancelled) reservation of the folio is checked
   out, the module creates a `survey.user_input` for the folio's main partner
   and queues the standard survey invitation email (with `scheduled_date`).
3. The folio gets a smart button **Survey** that opens the linked response.
4. Each `survey.user_input` carries the originating `folio_id` and
   `pms_property_id`, so you can filter and group responses by property in
   **Surveys / Participations**.

## Notes

- Only **one** survey per folio is generated. Reopening reservations and
  re-checking out does not create a new survey.
- If the folio's main partner has no email, no survey is scheduled
  (an informational log entry is written).
- The default survey is shipped with `noupdate="1"`; you can safely edit its
  questions and answers — module upgrades will not revert your changes.
