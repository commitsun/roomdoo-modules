from datetime import timedelta

from odoo import _, models

from odoo.addons.pms_ai_tool.tools import guarded_tool

from .pms_ai_operations_common import PROPERTY_IDS_INPUT

MAX_DAYS = 31
HIGH_OCCUPANCY_PCT = 95


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    @guarded_tool(
        input_schema={
            "date_from": {
                "type": "string",
                "format": "date",
                "description": "First night of the outlook (YYYY-MM-DD). "
                "Default: today.",
            },
            "days": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_DAYS,
                "description": f"Nights to show, 1 to {MAX_DAYS} (14).",
            },
            "property_ids": PROPERTY_IDS_INPUT,
        },
        output_schema={
            "totals": {"type": "object"},
            "by_property": {"type": "array", "items": {"type": "object"}},
        },
    )
    def _ai_pms_occupancy_outlook(self, date_from=None, days=14, property_ids=None):
        date_from = date_from or self._ai_ops_today()
        date_to = date_from + timedelta(days=days - 1)
        warnings = []
        scope = self._ai_ops_scope(property_ids, warnings)
        rooms = self._ai_ops_rooms_count(scope)
        nights = self._ai_ops_room_nights(date_from, date_to, scope)
        names = self._ai_ops_property_names(scope)
        dates = [date_from + timedelta(days=offset) for offset in range(days)]
        rows = []
        for prop_id in scope:
            total = rooms.get(prop_id, 0)
            daily = []
            for day in dates:
                counts = nights.get((prop_id, day), {"occupied": 0, "blocked": 0})
                occupied, blocked = counts["occupied"], counts["blocked"]
                used = occupied + blocked
                pct = round(100.0 * occupied / total, 1) if total else None
                entry = {
                    "date": day,
                    "occupied": occupied,
                    "blocked": blocked,
                    "available": max(total - used, 0),
                    "occupancy_pct": pct,
                }
                if total and used > total:
                    entry["flag"] = "overbooking"
                    entry["overbooked_rooms"] = used - total
                elif pct is not None and pct >= HIGH_OCCUPANCY_PCT:
                    entry["flag"] = "high"
                daily.append(entry)
            peak = max(
                daily,
                key=lambda entry: (
                    -1 if entry["occupancy_pct"] is None else entry["occupancy_pct"]
                ),
            )
            if not total:
                warnings.append(
                    _("%s has no rooms for overnight stays.") % names.get(prop_id, "")
                )
            rows.append(
                {
                    "property_id": prop_id,
                    "property": names.get(prop_id, ""),
                    "rooms": total,
                    "high_days": sum(1 for e in daily if e.get("flag") == "high"),
                    "overbooked_days": sum(
                        1 for e in daily if e.get("flag") == "overbooking"
                    ),
                    "peak_pct": peak["occupancy_pct"],
                    "peak_date": peak["date"],
                    "days": daily,
                }
            )
        rows.sort(
            key=lambda row: (
                -row["overbooked_days"],
                -row["high_days"],
                -(row["peak_pct"] or 0),
                row["property"],
            )
        )
        totals = {
            "properties": len(rows),
            "overbooked_property_days": sum(row["overbooked_days"] for row in rows),
            "high_property_days": sum(row["high_days"] for row in rows),
        }
        drafts = self.env["pms.reservation.line"].search_count(
            [
                ("date", ">=", date_from),
                ("date", "<=", date_to),
                ("pms_property_id", "in", scope),
                ("state", "=", "draft"),
                ("reservation_id.reservation_type", "!=", "out"),
            ]
        )
        if drafts:
            warnings.append(
                _(
                    "Room nights of pre-reservations (not confirmed) in the "
                    "period are not counted: %s."
                )
                % drafts
            )
        return {
            "summary": _(
                "%(overbooked)s property days overbooked and %(high)s more at "
                "%(pct)s%% or above between %(date_from)s and %(date_to)s."
            )
            % {
                "overbooked": totals["overbooked_property_days"],
                "high": totals["high_property_days"],
                "pct": HIGH_OCCUPANCY_PCT,
                "date_from": date_from,
                "date_to": date_to,
            },
            "period": {"date_from": date_from, "date_to": date_to},
            "definitions": [
                _(
                    "Occupied: nights of normal and staff reservations in active "
                    "rooms for overnight stays. Pre-reservations (not confirmed) "
                    "and cancelled reservations are not counted."
                ),
                _(
                    "Blocked: nights of out of service blocks. Available: rooms "
                    "that are neither occupied nor blocked."
                ),
                _(
                    "Rooms: active rooms for overnight stays today (the count of "
                    "past dates uses today's rooms). Occupancy: occupied nights "
                    "over rooms, as in the Alda KPIs, so blocked rooms are not "
                    "taken out of the denominator."
                ),
                _(
                    "Flag high: occupancy at %s%% or more. Flag overbooking: more "
                    "rooms used (occupied plus blocked) than the property has; "
                    "above 100%% occupancy the PMS allows overbooking."
                )
                % HIGH_OCCUPANCY_PCT,
            ],
            "warnings": warnings,
            "result": {
                "totals": totals,
                "by_property": self._ai_limit_rows(rows, warnings),
            },
        }
