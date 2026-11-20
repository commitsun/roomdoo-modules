System parameters, all optional:

* ``pms_ai_tool.environment``: label shown in every answer. Defaults to the
  ``DOODBA_ENVIRONMENT`` variable.
* ``pms_ai_tool.statement_timeout_ms``: statement timeout per call (15000).
* ``pms_ai_tool.max_rows``: row limit of the listings (50).
* ``pms_ai_tool.confirmation_ttl``: life of a confirmation token, in seconds
  (600).
* ``pms_ai_tool.write_enabled``: ``True`` to allow confirmed changes at all.
  Each write tool must also have *Write enabled* ticked.
* ``pms_ai_tool.log_max_chars``: size kept of each request, response and
  error in the MCP log (4000).
* ``pms_ai_tool.log_retention_days``: age at which MCP logs are purged (90).

In *AI > AI Tool*, set the users allowed to run each tool. Create one MCP
server per person with the tools they need, and a key for that person with an
expiration date.
