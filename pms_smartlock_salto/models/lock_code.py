import logging
from datetime import timedelta

from roomdoo_locks_base import LockError

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# Retention window: how long a revoked (suspended) Salto grant is kept before
# its user and access group are hard-deleted. The PIN is already disabled and
# the license freed at revoke time; this delay preserves the access logs for
# disputes.
_PURGE_AFTER_DAYS = 15


class LockCode(models.Model):
    _inherit = "lock.code"

    purged = fields.Boolean(
        default=False,
        copy=False,
        help="Salto only: the guest user and access group behind this "
        "credential's grant have been hard-deleted on the vendor, reclaiming "
        "the per-user license. Set by the retention cron once the dispute "
        "window has passed; the record itself is kept for audit.",
    )

    @api.model
    def _cron_purge_salto_grants(self):
        """Hard-delete Salto grants whose retention window has elapsed.

        Salto's ``revoke_access`` (run at checkout/cancel) only *suspends* the
        guest user: it frees the per-user license and disables the PIN but keeps
        the user, access group and audit logs. This cron reclaims those
        resources for good once ``_PURGE_AFTER_DAYS`` have passed since the
        credential's ``date_to``, calling the connector's ``delete_grant``.

        Other vendors (TTLock/Omnitec) delete on revoke and have nothing to
        purge, so the domain is scoped to Salto. A grant whose vendor call fails
        is left un-purged so the next run retries it; one bad grant never blocks
        the rest. The connector is built once per vendor to avoid re-auth per
        credential."""
        cutoff = fields.Datetime.now() - timedelta(days=_PURGE_AFTER_DAYS)
        codes = self.sudo().search(
            [
                ("vendor_id.vendor_type", "=", "salto"),
                ("cancelled", "=", True),
                ("purged", "=", False),
                ("vendor_grant_ref", "!=", False),
                ("date_to", "<", cutoff),
            ]
        )
        for vendor in codes.vendor_id:
            try:
                connector = vendor.get_connector()
            except Exception:
                _logger.warning(
                    "Salto purge: could not build connector for vendor %s; "
                    "retrying next run.",
                    vendor.display_name,
                    exc_info=True,
                )
                continue
            for code in codes.filtered(lambda c, vendor=vendor: c.vendor_id == vendor):
                try:
                    connector.delete_grant(code.vendor_grant_ref)
                except LockError:
                    _logger.warning(
                        "Salto purge: delete_grant failed for lock.code %s; "
                        "leaving un-purged for next run.",
                        code.id,
                        exc_info=True,
                    )
                    continue
                code.purged = True
