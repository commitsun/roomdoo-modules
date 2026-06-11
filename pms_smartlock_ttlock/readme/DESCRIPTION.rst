TTLock smart lock provider for the PMS.

Adds the ``ttlock`` vendor type to *PMS Smartlock Base* and implements its
connector on top of the ``roomdoo_locks_ttlock`` library, so the PMS can
grant, revoke and reveal PIN-based access on TTLock locks.

A single Roomdoo TTLock app is shared across hotels; each vendor record holds
the hotel's own TTLock account. The keypad confirm key defaults to ``#``.
