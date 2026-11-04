Add the tools to the MCP server of each person in *AI > AI Tool*. The first four
are read only. ``pms_partner_assign_salesperson`` changes data, so it also needs
an explicit list of allowed users, *Write enabled* ticked and the system
parameter ``pms_ai_tool.write_enabled`` set to ``True``.

A change is made in two calls: the first one (``confirm`` false) returns a
preview with the current salesperson of every company and a confirmation token;
the second one repeats the same arguments with ``confirm`` true and the token.
To undo it, run the tool again with the previous salesperson shown in the
preview (0 removes the salesperson).
