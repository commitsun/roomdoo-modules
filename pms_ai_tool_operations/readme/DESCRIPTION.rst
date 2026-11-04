Hotel operations tools for the AI assistants that use Odoo through MCP, on top
of the guardrails of ``pms_ai_tool``:

* ``pms_daily_movements``: arrivals, departures and rooms in house of a day,
  with the arrivals already checked in and the departures already out.
* ``pms_occupancy_outlook``: occupied, blocked and available rooms and
  occupancy of the coming nights, flagging the nights at 95% or more and the
  overbooked ones.
* ``pms_board_services_forecast``: units of each board service (breakfast,
  half board, dinner...) to prepare per property and day.
* ``pms_guest_data_pending``: arrivals whose guests still have to complete the
  check-in data, with the folios to chase and what is missing.
* ``pms_folio_reception_note``: posts an internal note in a folio, after a
  preview and an explicit confirmation.

The read tools return aggregated figures. Where guests are involved (pending
guest data, folio notes) names are shown as first name and initial and the
result is kept out of the MCP log.
