from datetime import datetime, time, timedelta

from odoo import api, fields, models

_TRIGGER_FIELDS = {
    "state",
    "checkin",
    "checkout",
    "arrival_hour",
    "departure_hour",
    "preferred_room_id",
    "reservation_line_ids",
}
_GENERATION_HORIZON = timedelta(hours=24)


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
        records._maybe_generate_lock_codes_on_change()
        return records

    def write(self, vals):
        result = super().write(vals)
        if _TRIGGER_FIELDS.intersection(vals):
            self._maybe_generate_lock_codes_on_change()
        if vals.get("state") == "cancel":
            self._cancel_lock_codes()
        return result

    @api.model
    def _cron_generate_lock_codes(self):
        now = fields.Datetime.now()
        today = now.date()
        candidates = self.search(
            [
                ("state", "not in", ("draft", "cancel")),
                ("checkin", ">=", today),
                ("checkin", "<=", today + timedelta(days=1)),
                ("reservation_line_ids.room_id.lock_vendor_id", "!=", False),
            ]
        )
        horizon = now + _GENERATION_HORIZON
        for reservation in candidates:
            checkin_dt = reservation.checkin_datetime
            if not checkin_dt or not (now < checkin_dt <= horizon):
                continue
            reservation._generate_lock_codes()

    def _maybe_generate_lock_codes_on_change(self):
        now = fields.Datetime.now()
        horizon = now + _GENERATION_HORIZON
        for reservation in self:
            if reservation.state in ("draft", "cancel"):
                continue
            checkin_dt = reservation.checkin_datetime
            if not checkin_dt or not (now < checkin_dt <= horizon):
                continue
            reservation._generate_lock_codes()

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
        """Manual trigger that bypasses the 24h horizon. Intended for QA
        and edge cases where the operator wants to force generation now."""
        for reservation in self:
            reservation._generate_lock_codes()

    def _generate_lock_codes(self):
        self.ensure_one()
        if self.lock_code_ids.filtered(
            lambda c: c.state in ("pending", "scheduled", "active")
        ):
            return
        LockCode = self.env["lock.code"]
        for room, date_from, date_to in self._build_lock_code_windows():
            code = LockCode.create(
                {
                    "reservation_id": self.id,
                    "room_id": room.id,
                    "vendor_id": room.lock_vendor_id.id,
                    "date_from": date_from,
                    "date_to": date_to,
                }
            )
            code._enqueue_sync("_sync_create")

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
