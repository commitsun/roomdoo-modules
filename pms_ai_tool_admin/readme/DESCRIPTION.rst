Accounting and administration tools of the PMS, served over MCP with the
guardrails of ``pms_ai_tool`` (read only, bounded periods, scoped to the
properties and companies of the user):

* ``pms_folios_pending_invoice``: finished folios that still have an amount to
  invoice, by property, with hints on why (no customer data, payments pending,
  partly invoiced).
* ``pms_customer_overdue``: overdue customer invoices in aging buckets, by
  property, with the largest debtors. It needs accounting access.
* ``pms_cash_session_differences``: differences recorded when closing the cash
  sessions of the hotels (cash counted against the theoretical balance).
