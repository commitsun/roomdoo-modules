AI tools for hotel support, served over MCP with the guardrails of
``pms_ai_tool``:

* ``pms_folio_360``: overview of a folio by id or code (state, property, dates,
  channel, customer with masked e-mail and mobile, reservations, amounts with
  and without VAT, payments, invoices and guest notifications by state).
* ``pms_folio_notifications``: notification logs of a folio (rule, template,
  channel, state, error, HTTP status of BookAI when installed) with a plain
  explanation of why a message was skipped, failed or is still waiting.

Both are read only, limited to the properties of the user, and keep the
personal data out of the MCP log.
