import random
from datetime import timezone

from roomdoo_locks_base import (
    LockConnectionError,
    LockError,
    LockOfflineError,
)

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.osv import expression

from odoo.addons.queue_job.exception import RetryableJobError

_TRANSIENT_RETRY_SECONDS = 300

# A vendor gateway cannot service two requests at once: two jobs programming
# the same physical lock concurrently make the second fail with "busy". We
# serialise per ``lock_device_id`` with a PostgreSQL transaction-level advisory
# lock taken before the vendor call. ``_GATEWAY_LOCK_CLASSID`` namespaces these
# locks (first arg of the two-int ``pg_try_advisory_xact_lock``) so they never
# collide with advisory locks taken elsewhere (e.g. queue_job's jobrunner).
_GATEWAY_LOCK_CLASSID = 0x10C  # "LOCK"
# A job that loses the race re-enqueues after this delay (base + jitter) to
# spread retries and avoid a thundering herd on the contended gateway.
_GATEWAY_LOCK_RETRY_BASE = 10
_GATEWAY_LOCK_RETRY_JITTER = 20

_SYNCING_JOB_STATES = ("pending", "enqueued", "started")

_STATE_SELECTION = [
    ("pending", "Pending"),
    ("syncing", "Syncing"),
    ("scheduled", "Scheduled"),
    ("active", "Active"),
    ("expired", "Expired"),
    ("failed", "Failed"),
    ("cancelled", "Cancelled"),
]


class LockCode(models.Model):
    """A guest credential: one PIN valid on a set of locks for a window.

    The credential covers the guest's room lock plus any shared/common
    doors the room grants (``pms.room.shared_lock_ids``), all under the
    same PIN. How that single credential is realised across the locks is
    the vendor connector's concern; Odoo only stores the PIN and an opaque
    ``vendor_grant_ref`` handed back to modify/revoke the grant. The set of
    locks the grant was requested for is snapshotted in ``target_ids`` for
    visibility.
    """

    _name = "lock.code"
    _description = "Lock Code"

    pin = fields.Char(
        groups="!base.group_user",
        help="PIN code the guest types on the keypad of every lock in the "
        "grant. Returned by the vendor connector. Field-level group locks "
        "down direct read for every internal user (admin included); the only "
        "way to surface the value is the audited ``action_reveal_pin`` "
        "button, which sudoes the read and records access in "
        "``lock.code.access.log``.",
    )
    vendor_id = fields.Many2one(
        comodel_name="lock.vendor",
        string="Vendor",
        required=True,
        ondelete="restrict",
    )
    vendor_grant_ref = fields.Char(
        string="Vendor Grant Ref",
        copy=False,
        help="Opaque handle returned by the vendor connector for this access "
        "grant. Required to modify or revoke the grant. Not parsed by Odoo.",
    )
    reservation_id = fields.Many2one(
        comodel_name="pms.reservation",
        string="Reservation",
        ondelete="cascade",
        index=True,
    )
    room_id = fields.Many2one(
        comodel_name="pms.room",
        string="Room",
        required=True,
        ondelete="cascade",
        index=True,
        help="Room whose guest this credential belongs to.",
    )
    target_ids = fields.One2many(
        comodel_name="lock.code.target",
        inverse_name="lock_code_id",
        string="Locks",
        help="Snapshot of the locks this credential's grant covers (room "
        "door + shared common doors).",
    )
    date_from = fields.Datetime(required=True)
    date_to = fields.Datetime(required=True)
    cancelled = fields.Boolean(
        default=False,
        copy=False,
        help="Set to True when the grant has been revoked on the locks.",
    )
    failed = fields.Boolean(
        default=False,
        copy=False,
        help="Set to True when a sync attempt failed permanently "
        "(non-retryable error from the vendor).",
    )
    state = fields.Selection(
        selection=_STATE_SELECTION,
        compute="_compute_state",
        search="_search_state",
        help="Lifecycle of the credential, derived from sync status and "
        "validity window: pending (not granted yet, no job in flight), "
        "syncing (a vendor sync job is enqueued or running), scheduled "
        "(granted, awaiting date_from), active (within validity window), "
        "expired (past date_to), failed (sync error), cancelled (revoked).",
    )
    access_log_ids = fields.One2many(
        comodel_name="lock.code.access.log",
        inverse_name="lock_code_id",
        string="PIN Access Log",
        readonly=True,
        groups="pms_smartlock_base.group_smartlock_admin",
    )
    queue_job_ids = fields.Many2many(
        comodel_name="queue.job",
        relation="lock_code_queue_job_rel",
        column1="lock_code_id",
        column2="job_id",
        string="Sync Jobs",
        copy=False,
        help="Queue jobs that have synchronised this credential with the "
        "vendor. Useful to inspect retries, errors and traces from a failed "
        "sync.",
    )
    active = fields.Boolean(default=True)

    def name_get(self):
        return [
            (
                rec.id,
                f"{rec.room_id.display_name or '?'} · {rec.date_from or ''}",
            )
            for rec in self
        ]

    @api.depends(
        "cancelled",
        "failed",
        "vendor_grant_ref",
        "date_from",
        "date_to",
        "queue_job_ids.state",
    )
    def _compute_state(self):
        now = fields.Datetime.now()
        for record in self:
            if record.cancelled:
                record.state = "cancelled"
            elif record.failed:
                record.state = "failed"
            elif any(j.state in _SYNCING_JOB_STATES for j in record.queue_job_ids):
                record.state = "syncing"
            elif not record.vendor_grant_ref:
                record.state = "pending"
            elif record.date_to and record.date_to <= now:
                record.state = "expired"
            elif record.date_from and record.date_from > now:
                record.state = "scheduled"
            else:
                record.state = "active"

    def _search_state(self, operator, value):
        all_states = {s[0] for s in _STATE_SELECTION}
        if isinstance(value, list | tuple):
            values = set(value)
        else:
            values = {value}
        if operator in ("!=", "not in"):
            values = all_states - values
        elif operator not in ("=", "in"):
            raise ValueError(f"Unsupported operator '{operator}' on lock.code.state")
        values &= all_states
        if not values:
            return [("id", "=", False)]

        now = fields.Datetime.now()
        base_active_not = [("cancelled", "=", False), ("failed", "=", False)]
        # ``syncing`` is derived from queue_job_ids and overrides the
        # date/vendor_grant_ref-driven states. The other "live" branches
        # (pending/scheduled/active/expired) must therefore exclude codes
        # with a job in flight to keep the state mutually exclusive.
        syncing_ids = (
            self.env["lock.code"]
            .with_context(active_test=False)
            .search(
                base_active_not
                + [("queue_job_ids.state", "in", list(_SYNCING_JOB_STATES))]
            )
            .ids
        )
        not_syncing = [("id", "not in", syncing_ids)]
        state_domains = {
            "cancelled": [("cancelled", "=", True)],
            "failed": base_active_not[:1] + [("failed", "=", True)],
            "syncing": [("id", "in", syncing_ids)],
            "pending": base_active_not
            + not_syncing
            + [("vendor_grant_ref", "=", False)],
            "expired": base_active_not
            + not_syncing
            + [("vendor_grant_ref", "!=", False), ("date_to", "<=", now)],
            "scheduled": base_active_not
            + not_syncing
            + [("vendor_grant_ref", "!=", False), ("date_from", ">", now)],
            "active": base_active_not
            + not_syncing
            + [
                ("vendor_grant_ref", "!=", False),
                ("date_from", "<=", now),
                ("date_to", ">", now),
            ],
        }
        return expression.OR([state_domains[v] for v in values])

    @staticmethod
    def _to_utc(dt):
        """Odoo stores naive UTC datetimes; the library requires UTC aware."""
        return dt.replace(tzinfo=timezone.utc)

    def _local_tz(self):
        """IANA timezone the vendor's hardware enforces schedules against — the
        hotel's local timezone (one hotel, one timezone). Passed to the
        connector on the context so user-centric vendors that store naive
        wall-clock (Salto) can localize the UTC window; others ignore it."""
        return self.reservation_id.pms_property_id.tz or self.room_id.pms_property_id.tz

    @staticmethod
    def _to_naive(dt):
        """Strip tzinfo from an aware UTC datetime for Odoo storage."""
        return dt.astimezone(timezone.utc).replace(tzinfo=None)

    def _grant_target_specs(self):
        """Return the lock set this credential's grant covers: the room's own
        lock plus the active shared/common doors the room grants. Each spec is
        a dict ready to create a ``lock.code.target``."""
        self.ensure_one()
        room = self.room_id
        specs = []
        if room.lock_device_id:
            specs.append(
                {
                    "kind": "room",
                    "lock_device_id": room.lock_device_id,
                    "room_id": room.id,
                }
            )
        for common in room.shared_lock_ids.filtered(
            lambda c: c.active and c.lock_device_id
        ):
            specs.append(
                {
                    "kind": "common",
                    "lock_device_id": common.lock_device_id,
                    "common_lock_id": common.id,
                }
            )
        return specs

    def _apply_grant(self, grant, specs=None):
        """Persist the credential from a vendor ``AccessGrant``. When ``specs``
        is given (initial grant) the ``target_ids`` snapshot is rebuilt; on a
        modify the targets are unchanged and only the PIN/ref/window move.

        A ``grant.pin`` of ``None`` means *unchanged*: the vendor kept the same
        credential (and may be unable to read it back, e.g. Salto on a window
        change), so we keep the stored PIN instead of overwriting it. An empty
        string would be a real value and is persisted as such."""
        self.ensure_one()
        vals = {
            "vendor_grant_ref": grant.ref,
            "date_from": self._to_naive(grant.starts_at),
            "date_to": self._to_naive(grant.ends_at),
            # A successful sync clears any prior permanent failure.
            "failed": False,
        }
        if grant.pin is not None:
            vals["pin"] = grant.pin
        if specs is not None:
            vals["target_ids"] = [(5, 0, 0)] + [(0, 0, spec) for spec in specs]
        self.write(vals)

    def _persist_failed(self):
        """Mark the code as permanently failed in an independent transaction.

        The sync methods re-raise after a non-retryable ``LockError`` so
        queue_job records the traceback, but that rollback also discards any
        write made in the job cursor. Writing ``failed`` through a separate
        cursor keeps the flag and prevents the code from falling back to
        ``pending``."""
        self.ensure_one()
        with self.env.registry.cursor() as failed_cr:
            self.with_env(self.env(cr=failed_cr)).sudo().failed = True

    def _try_lock_gateways(self, device_ids):
        """Acquire a transaction-level advisory lock per ``lock_device_id``
        this operation will program, so no two jobs hit the same gateway at
        once. Returns True only if every lock was acquired; False as soon as
        one is held by another job's transaction. Locks already acquired in
        this call are released when the job's transaction ends — which, on a
        False result, happens immediately because the caller raises
        ``RetryableJobError`` and queue_job rolls back. Ids are sorted for a
        deterministic acquisition order."""
        cr = self.env.cr
        for device_id in sorted(set(device_ids)):
            cr.execute(
                "SELECT pg_try_advisory_xact_lock(%s, hashtext(%s))",
                (_GATEWAY_LOCK_CLASSID, device_id),
            )
            if not cr.fetchone()[0]:
                return False
        return True

    def _raise_gateway_busy(self):
        """Re-enqueue the sync because another job holds a gateway it needs.
        ``ignore_retry=True`` keeps this off the ``max_retries`` counter: a
        credential must never end up ``failed`` just for losing a gateway
        race, only for a real vendor error."""
        raise RetryableJobError(
            "Gateway busy: another sync holds one of these locks; retrying",
            seconds=_GATEWAY_LOCK_RETRY_BASE
            + random.randint(0, _GATEWAY_LOCK_RETRY_JITTER),
            ignore_retry=True,
        )

    def _sync_create(self):
        self.ensure_one()
        # Vendor sync jobs run under the enqueueing operator's user. ACL
        # restricts lock.code mutations to the smartlock admin group, so we
        # elevate to sudo for the duration of the sync.
        self = self.sudo()
        if self.cancelled:
            return
        specs = self._grant_target_specs()
        if not self._try_lock_gateways([s["lock_device_id"] for s in specs]):
            self._raise_gateway_busy()
        try:
            # Pass the reservation on the context so user-centric vendors (Salto)
            # can name the credential after the guest. Passcode vendors ignore
            # it. Only the create path needs it; modify/revoke don't.
            connector = self.vendor_id.with_context(
                smartlock_grant_reservation=self.reservation_id,
                smartlock_local_tz=self._local_tz(),
            ).get_connector()
            grant = connector.grant_access(
                lock_ids=[s["lock_device_id"] for s in specs],
                starts_at=self._to_utc(self.date_from),
                ends_at=self._to_utc(self.date_to),
            )
        except (LockConnectionError, LockOfflineError) as exc:
            raise RetryableJobError(str(exc), seconds=_TRANSIENT_RETRY_SECONDS) from exc
        except LockError:
            self._persist_failed()
            raise
        self._apply_grant(grant, specs=specs)
        self.invalidate_recordset(["cancelled"])
        if self.cancelled:
            self._enqueue_sync("_sync_remove")

    def _sync_modify(self, date_from, date_to):
        self.ensure_one()
        self = self.sudo()
        if not self._try_lock_gateways(self.target_ids.mapped("lock_device_id")):
            self._raise_gateway_busy()
        try:
            connector = self.vendor_id.with_context(
                smartlock_local_tz=self._local_tz()
            ).get_connector()
            grant = connector.modify_access(
                grant_ref=self.vendor_grant_ref,
                starts_at=self._to_utc(date_from),
                ends_at=self._to_utc(date_to),
                # Pass the known PIN so vendors whose ref handle can go stale
                # (TESA pre-assignment auto-activating into a check-in) can
                # re-resolve live state and confirm the credential is ours. We
                # are under sudo; the PIN stays in the restricted field and is
                # never written to vendor_grant_ref.
                pin=self.pin,
            )
        except (LockConnectionError, LockOfflineError) as exc:
            raise RetryableJobError(str(exc), seconds=_TRANSIENT_RETRY_SECONDS) from exc
        except LockError:
            self._persist_failed()
            raise
        self._apply_grant(grant)

    def _sync_remove(self):
        self.ensure_one()
        self = self.sudo()
        if not self._try_lock_gateways(self.target_ids.mapped("lock_device_id")):
            self._raise_gateway_busy()
        try:
            connector = self.vendor_id.get_connector()
            # PIN passed for the same reason as in _sync_modify: let vendors
            # with stale-prone ref handles confirm the stay is ours before
            # clearing it, so we never revoke a stranger's access.
            connector.revoke_access(grant_ref=self.vendor_grant_ref, pin=self.pin)
        except (LockConnectionError, LockOfflineError) as exc:
            raise RetryableJobError(str(exc), seconds=_TRANSIENT_RETRY_SECONDS) from exc
        except LockError:
            self._persist_failed()
            raise
        self.cancelled = True

    def _enqueue_sync(self, method_name, **kwargs):
        """Enqueue ``method_name`` via queue_job and link the resulting
        ``queue.job`` so the hotel can inspect retries and errors from the
        ``lock.code`` form."""
        self.ensure_one()
        # A fresh sync supersedes any prior permanent failure: clear the flag
        # so the code reflects the new attempt (syncing → active/failed) instead
        # of staying stuck in ``failed``.
        if self.sudo().failed:
            self.sudo().failed = False
        delayed = getattr(self.with_delay(), method_name)(**kwargs)
        job = self.env["queue.job"].search([("uuid", "=", delayed.uuid)], limit=1)
        if job:
            self.sudo().queue_job_ids |= job
        return delayed

    def action_reveal_pin(self):
        """Audited PIN reveal for non-admin operators. The ``pin`` field has
        ``groups=group_smartlock_admin``, so only admins see it via direct
        ORM/RPC reads. Operators reveal it via this action, which records
        the access in ``lock.code.access.log`` before opening a transient
        viewer with the value."""
        self.ensure_one()
        if not self.sudo().pin:
            raise UserError(
                _("PIN not generated yet. Wait until the vendor sync completes.")
            )
        self.env["lock.code.access.log"].sudo().create(
            {"lock_code_id": self.id, "user_id": self.env.user.id}
        )
        viewer = (
            self.env["lock.code.pin.viewer"]
            .sudo()
            .create(
                {
                    "lock_code_id": self.id,
                    "pin": self.sudo().pin,
                    "pin_confirm_key": self.sudo().vendor_id.pin_confirm_key,
                }
            )
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("PIN"),
            "res_model": "lock.code.pin.viewer",
            "view_mode": "form",
            "target": "new",
            "res_id": viewer.id,
        }
