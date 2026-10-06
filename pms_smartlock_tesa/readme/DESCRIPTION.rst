TESA Smartair smart lock provider for the PMS.

Adds the ``tesa`` vendor type to *PMS Smartlock Base* and implements its
connector on top of the ``roomdoo_locks_tesa`` library, so the PMS can grant,
revoke and reveal PIN-based access on TESA Smartair locks.

TESA has no cloud service: Odoo connects to each hotel's own on-prem Smartair
server, so the host and operator credentials live on the vendor record (not in
the deployment environment). The keypad confirm key defaults to ``✓``.
