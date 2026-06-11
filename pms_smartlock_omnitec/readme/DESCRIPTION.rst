Omnitec / Rent&Pass smart lock provider for the PMS.

Adds the ``omnitec`` vendor type to *PMS Smartlock Base* and implements its
connector on top of the ``roomdoo_locks_omnitec`` library, so the PMS can
grant, revoke and reveal PIN-based access on Omnitec OsAccess locks.

Omnitec ships two OsAccess generations (*modern* and *legacy*) served by
different Roomdoo apps; the vendor record selects which generation it talks
to. The keypad confirm key defaults to ``#``.
