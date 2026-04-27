from datetime import datetime, time, timedelta

from odoo import api, fields, models

_TRIGGER_FIELDS = {
    "state",
    "checkin",
    "checkout",
    "arrival_hour",
    "departure_hour",
    "reservation_type",
}
_GENERATION_HORIZON = timedelta(hours=24)
_PRECOMMIT_PENDING_KEY = "pms_smartlock.pending_sync_ids"


class PmsReservation(models.Model):
    _inherit = "pms.reservation"

    lock_code_ids = fields.One2many(
        comodel_name="lock.code",
        inverse_name="reservation_id",
        string="Lock Codes",
    )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._enqueue_lock_sync()
        return records

    def write(self, vals):
        result = super().write(vals)
        if _TRIGGER_FIELDS.intersection(vals):
            self._enqueue_lock_sync()
        if vals.get("state") == "cancel":
            self._cancel_lock_codes()
        return result

    @api.model
    def _cron_generate_lock_codes(self):
        today = fields.Datetime.now().date()
        candidates = self.search(
            [
                ("state", "not in", ("draft", "cancel")),
                ("checkin", ">=", today),
                ("checkin", "<=", today + timedelta(days=1)),
                ("reservation_line_ids.room_id.lock_vendor_id", "!=", False),
            ]
        )
        for reservation in candidates:
            if reservation._should_have_lock_codes():
                reservation._sync_lock_codes()

    def _enqueue_lock_sync(self):
        """Defer the sync to ``cr.precommit`` so multiple listeners in the
        same transaction (e.g. a ``preferred_room_id`` write cascading to
        ``line.room_id``) coalesce into one call per reservation. Running
        ``_sync_lock_codes`` directly per listener could enqueue duplicate
        modifies, which on vendor paths that fall back to delete+create
        would regenerate the PIN twice.

        ``cr.precommit`` is the cursor's pre-commit callback queue, run
        from inside ``cr.flush()`` (which ``cr.commit()`` calls before the
        SQL ``COMMIT``). The hook therefore runs inside the same
        transaction as the change that triggered it — if it rolls back, so
        do the jobs we enqueued. Same pattern as ``mail.thread._track_finalize``."""
        if not self:
            return
        pending = self.env.cr.precommit.data.setdefault(_PRECOMMIT_PENDING_KEY, set())
        if not pending:
            self.env.cr.precommit.add(self.env["pms.reservation"]._flush_lock_syncs)
        pending.update(self.ids)

    @api.model
    def _flush_lock_syncs(self):
        ids = self.env.cr.precommit.data.pop(_PRECOMMIT_PENDING_KEY, set())
        if not ids:
            return
        for reservation in self.browse(ids).exists():
            if reservation._should_have_lock_codes():
                reservation._sync_lock_codes()

    def _should_have_lock_codes(self):
        """True when the reservation is confirmed and either already has
        live codes (system is committed) or is within
        ``_GENERATION_HORIZON`` of checkin."""
        self.ensure_one()
        if self.state in ("draft", "cancel"):
            return False
        # "out" reservations are room blockers without a guest. Any other
        # current or future type qualifies for codes.
        if self.reservation_type != "out":
            if self.lock_code_ids.filtered(
                lambda c: c.state in ("pending", "scheduled", "active")
            ):
                return True
            now = fields.Datetime.now()
            return bool(
                self.checkin_datetime
                and now < self.checkin_datetime <= now + _GENERATION_HORIZON
            )
        return False

    def _sync_lock_codes(self):
        """Reconcile ``lock.code`` records against the reservation's current
        room windows: cancel codes whose room is no longer assigned, modify
        codes whose window shifted, create codes for newly-assigned rooms.

        Pending codes (no ``vendor_code_id`` yet) keep their original dates
        even if the reservation window shifted — the ``_sync_create`` job
        already enqueued will use whatever dates were on the record at
        enqueue time. Updating them here would write vendor-bound state
        before the vendor confirmed."""
        self.ensure_one()
        LockCode = self.env["lock.code"]
        target = {
            room.id: (room, date_from, date_to)
            for room, date_from, date_to in self._build_lock_code_windows()
        }
        live_by_room = {
            code.room_id.id: code
            for code in self.lock_code_ids.filtered(
                lambda c: c.state in ("pending", "scheduled", "active")
            )
        }
        for room_id, code in live_by_room.items():
            if room_id not in target:
                if code.vendor_code_id:
                    code._enqueue_sync("_sync_remove")
                else:
                    code.cancelled = True
                continue
            _, new_from, new_to = target[room_id]
            if code.date_from == new_from and code.date_to == new_to:
                continue
            if code.vendor_code_id:
                code._enqueue_sync("_sync_modify", date_from=new_from, date_to=new_to)
        for room_id, (room, date_from, date_to) in target.items():
            if room_id in live_by_room:
                continue
            code = LockCode.create(
                {
                    "reservation_id": self.id,
                    "room_id": room_id,
                    "vendor_id": room.lock_vendor_id.id,
                    "date_from": date_from,
                    "date_to": date_to,
                }
            )
            code._enqueue_sync("_sync_create")

    def _cancel_lock_codes(self):
        """For each reservation in self, invalidate any lock.code that is
        still live (not already cancelled/failed). Codes already synced to
        the vendor are removed asynchronously; codes that never reached the
        vendor are marked cancelled locally (the create guard prevents any
        subsequent vendor call)."""
        for reservation in self:
            for code in reservation.lock_code_ids.filtered(
                lambda c: not c.cancelled and not c.failed
            ):
                if code.vendor_code_id:
                    code._enqueue_sync("_sync_remove")
                else:
                    code.cancelled = True

    def action_generate_lock_codes(self):
        """Force a sync of lock codes regardless of the horizon. For QA
        and operator-driven force."""
        for reservation in self:
            if reservation.state in ("draft", "cancel"):
                continue
            reservation._sync_lock_codes()

    def _build_lock_code_windows(self):
        """Return ``[(room, date_from, date_to), ...]`` grouping
        ``reservation_line_ids`` by contiguous ``room_id``.

        First group starts at ``checkin_datetime``; last ends at
        ``checkout_datetime``; intermediate transitions split at the
        property's ``default_departure_hour`` of the transition date so the
        outgoing room remains valid until standard checkout while the
        incoming one becomes valid at the same instant.

        Only rooms with both ``lock_vendor_id`` and ``lock_device_id`` are
        returned. Datetimes are naive UTC (Odoo storage convention).
        """
        self.ensure_one()
        lines = self.reservation_line_ids.sorted("date")
        if not lines:
            return []

        groups = []
        for line in lines:
            if not line.room_id:
                continue
            if groups and groups[-1][0] == line.room_id:
                groups[-1] = (groups[-1][0], groups[-1][1], line)
            else:
                groups.append((line.room_id, line, line))

        windows = []
        for idx, (room, first, _last) in enumerate(groups):
            if not (room.lock_vendor_id and room.lock_device_id):
                continue
            if idx == 0:
                date_from = self.checkin_datetime
            else:
                date_from = self._lock_code_property_dt(
                    first.date, self.pms_property_id.default_departure_hour
                )
            if idx == len(groups) - 1:
                date_to = self.checkout_datetime
            else:
                next_first_date = groups[idx + 1][1].date
                date_to = self._lock_code_property_dt(
                    next_first_date,
                    self.pms_property_id.default_departure_hour,
                )
            windows.append((room, date_from, date_to))
        return windows

    def _lock_code_property_dt(self, local_date, hour_str):
        self.ensure_one()
        hour = int(hour_str[0:2])
        minute = int(hour_str[3:5])
        local_dt = datetime.combine(local_date, time(hour, minute))
        return self.pms_property_id.date_property_timezone(local_dt)
