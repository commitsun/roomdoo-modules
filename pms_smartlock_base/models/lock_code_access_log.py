from datetime import timedelta

from odoo import api, fields, models


class LockCodeAccessLog(models.Model):
    _name = "lock.code.access.log"
    _description = "Lock Code PIN Access Log"
    _order = "accessed_at desc"
    _rec_name = "accessed_at"

    lock_code_id = fields.Many2one(
        comodel_name="lock.code",
        required=True,
        ondelete="cascade",
        index=True,
    )
    user_id = fields.Many2one(
        comodel_name="res.users",
        required=True,
        index=True,
        default=lambda self: self.env.user,
    )
    accessed_at = fields.Datetime(
        required=True,
        default=fields.Datetime.now,
    )
    reservation_id = fields.Many2one(
        comodel_name="pms.reservation",
        related="lock_code_id.reservation_id",
        store=True,
        index=True,
    )
    room_id = fields.Many2one(
        comodel_name="pms.room",
        related="lock_code_id.room_id",
        store=True,
    )

    @api.model
    def _cron_purge_old(self, delete_older_than=90):
        """Cron entry point. Default retention is 90 days; override by
        editing the cron's code (e.g. ``model._cron_purge_old(30)``)
        per-tenant.

        Sudo on unlink: ACL is read-only even for admins; the audit log
        is meant to be immutable from the UI, but the cron must be able
        to age out old entries."""
        cutoff = fields.Datetime.now() - timedelta(days=delete_older_than)
        self.sudo().search([("accessed_at", "<", cutoff)]).unlink()
