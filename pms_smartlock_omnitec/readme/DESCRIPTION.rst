Omnitec / Rent&Pass smart lock provider for the PMS.

Adds the ``omnitec`` vendor type to *PMS Smartlock Base* and implements its
connector on top of the ``roomdoo_locks_omnitec`` library, so the PMS can
grant, revoke and reveal PIN-based access on Omnitec OsAccess locks.

Omnitec ships two OsAccess generations (*modern* and *legacy*) served by
different Roomdoo apps; the vendor record selects which generation it talks
to. The keypad confirm key defaults to ``#``.

Each guest credential is pushed to every lock of its grant as a passcode named
after the booking and the credential (e.g. ``F2600001#17``), which is also how
it shows in the Omnitec app. A grant interrupted halfway (a lock that did not
answer through its gateway) keeps what it already programmed, and the next retry
resumes it with the same PIN instead of starting over. If no retry completes it,
the hourly *Smartlocks: Clean up unfinished Omnitec grants* job deletes those
passcodes from the locks.

When a lock does not complete a command through its gateway (errcode ``-1``),
the sync is retried after ten seconds twice, as Omnitec advises, and then at
longer intervals. Other vendors keep the default spacing.
