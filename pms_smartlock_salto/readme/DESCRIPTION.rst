Salto KS smart lock provider for the PMS.

Adds the ``salto`` vendor type to *PMS Smartlock Base* and implements its
connector on top of the ``roomdoo_locks_salto`` library, so the PMS can grant,
revoke and reveal PIN-based access on Salto KS locks.

Salto KS is *user-centric*: a guest is a site user assigned to an access group
that links the locks for a validity window. Because Salto bills per subscribed
user, revoking a grant only **suspends** the user (frees the license and
disables the PIN); a retention cron hard-deletes the suspended user and its
access group 15 days after checkout, keeping the audit trail until then.

The guest's name (never the email, to avoid Salto emailing the guest) is set on
the site user and the reservation locator labels the access group, so the hotel
can tell whose credential is whose. The keypad confirm key defaults to the
Enter symbol ``↵``.
