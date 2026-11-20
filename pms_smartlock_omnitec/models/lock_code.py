import logging

from roomdoo_locks_base import LockError

from odoo import api, fields, models

from odoo.addons.pms_smartlock_base.models.lock_code import _SYNCING_JOB_STATES

_logger = logging.getLogger(__name__)


class LockCode(models.Model):
    _inherit = "lock.code"

    def _omnitec_passcode_tag(self):
        """Name given on Omnitec to every passcode of this credential.

        Stable across sync attempts and unique per credential, so a grant
        interrupted halfway is found and resumed instead of programmed twice,
        and what it left is found if it is never completed. It shows in the
        Omnitec app, hence the booking reference."""
        self.ensure_one()
        return f"{self.reservation_id.name or ''}#{self.id}"

    @api.model
    def _cron_purge_omnitec_leftovers(self):
        """Delete from the locks the passcodes of Omnitec grants that were
        never completed.

        A grant interrupted halfway keeps the passcodes it already programmed
        so the next attempt can resume it. If no attempt completes it (attempts
        exhausted, or the credential cancelled between two of them), Odoo never
        gets a ``vendor_grant_ref`` to revoke them, and they would stay valid,
        under a PIN nobody was given, until the window ends. They are found by
        the credential's tag.

        Candidates are Omnitec credentials never granted, failed or cancelled,
        whose create job reached the vendor without completing (it failed, or
        it was retried), with no sync in flight and a window still open. They
        are checked on every run until the window ends: deleting is idempotent,
        and relaunching the dead job by hand can leave passcodes again. Locks
        busy with another sync are left for the next run."""
        codes = (
            self.sudo()
            .with_context(active_test=False)
            .search(
                [
                    ("vendor_id.vendor_type", "=", "omnitec"),
                    ("vendor_grant_ref", "=", False),
                    "|",
                    ("failed", "=", True),
                    ("cancelled", "=", True),
                    ("date_to", ">", fields.Datetime.now()),
                    "|",
                    ("queue_job_ids.state", "=", "failed"),
                    ("queue_job_ids.retry", ">", 1),
                ]
            )
            .filtered(
                lambda c: not any(
                    job.state in _SYNCING_JOB_STATES for job in c.queue_job_ids
                )
            )
        )
        for vendor in codes.vendor_id:
            try:
                connector = vendor.get_connector()
            except Exception:
                _logger.warning(
                    "Omnitec cleanup: could not build connector for vendor %s; "
                    "retrying next run.",
                    vendor.display_name,
                    exc_info=True,
                )
                continue
            for code in codes.filtered(lambda c, vendor=vendor: c.vendor_id == vendor):
                lock_ids = [
                    spec["lock_device_id"] for spec in code._grant_target_specs()
                ]
                if not code._try_lock_gateways(lock_ids):
                    continue
                try:
                    deleted = connector.delete_tagged_passcodes(
                        lock_ids, code._omnitec_passcode_tag()
                    )
                except LockError:
                    _logger.warning(
                        "Omnitec cleanup: could not delete the passcodes left by "
                        "lock.code %s; retrying next run.",
                        code.id,
                        exc_info=True,
                    )
                    continue
                if deleted:
                    _logger.info(
                        "Omnitec cleanup: deleted %s passcode(s) left by lock.code %s.",
                        deleted,
                        code.id,
                    )
