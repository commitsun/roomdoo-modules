Guardrails for the AI tools that Odoo serves over MCP (``ai_oca_mcp``), and the
home of the PMS tools.

A tool is a private method of ``pms.ai.tools`` decorated with
``guarded_tool``. Every call:

* validates its arguments against the declared schema and refuses unknown
  ones, so no client can send a domain or raw SQL;
* runs under a statement timeout (15 s by default);
* for read tools, runs in a read-only transaction that is always rolled back:
  an accidental write fails and nothing is saved;
* for write tools, needs two calls. The first one returns a preview and a
  token signed with the database secret and bound to the user, the tool, the
  arguments and the data the preview saw; the second one must repeat the
  same arguments with the token, and it aborts if anything changed or the
  token expired (10 minutes by default);
* returns a common envelope with the environment, period, definitions, data
  quality warnings and generation time;
* fails with a plain message, never with a traceback.

On top of ``ai_tool`` and ``ai_oca_mcp`` the module adds:

* the users allowed to run each tool. Write tools always need an explicit
  list, and ``tools/list`` only shows the tools the key owner may run;
* a per-tool and a global switch for confirmed changes;
* MCP annotations (``readOnlyHint``, ``destructiveHint``);
* the user, tool and duration of each call in the MCP log, with truncated
  payloads and a daily purge;
* immediate revocation: archiving a server, or expiring, changing or deleting
  a key, cuts access at once instead of waiting for the key cache to expire.
