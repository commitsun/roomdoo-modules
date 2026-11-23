from datetime import timedelta

from odoo import _, models

from odoo.addons.pms_ai_tool.tools import guarded_tool

from .pms_ai_operations_common import PROPERTY_IDS_INPUT

MAX_DAYS = 90
DEFAULT_DAYS = 30
DEFAULT_HOT_PCT = 95
DEFAULT_COLD_PCT = 40
DEFAULT_COLD_DAYS = 14


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    @guarded_tool(
        input_schema={
            "days": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_DAYS,
                "description": f"Nights ahead to look at, from today, 1 to {MAX_DAYS} "
                f"({DEFAULT_DAYS}).",
            },
            "hot_threshold_pct": {
                "type": "number",
                "minimum": 1,
                "maximum": 300,
                "description": "A night is hot at this occupancy or more, or when "
                f"it is overbooked ({DEFAULT_HOT_PCT}).",
            },
            "cold_threshold_pct": {
                "type": "number",
                "minimum": 0,
                "maximum": 100,
                "description": "A night is cold below this occupancy "
                f"({DEFAULT_COLD_PCT}).",
            },
            "cold_horizon_days": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_DAYS,
                "description": "Cold nights are only looked for in the first days "
                f"of the outlook, the ones close to today ({DEFAULT_COLD_DAYS}).",
            },
            "property_ids": PROPERTY_IDS_INPUT,
        },
        output_schema={
            "totals": {"type": "object"},
            "by_property": {"type": "array", "items": {"type": "object"}},
        },
    )
    def _ai_pms_hot_cold_dates(
        self,
        days=DEFAULT_DAYS,
        hot_threshold_pct=DEFAULT_HOT_PCT,
        cold_threshold_pct=DEFAULT_COLD_PCT,
        cold_horizon_days=DEFAULT_COLD_DAYS,
        property_ids=None,
    ):
        date_from = self._ai_ops_today()
        date_to = date_from + timedelta(days=days - 1)
        cold_to = date_from + timedelta(days=min(cold_horizon_days, days) - 1)
        warnings = []
        scope = self._ai_ops_scope(property_ids, warnings)
        rooms = self._ai_ops_rooms_count(scope)
        nights = self._ai_ops_room_nights(date_from, date_to, scope)
        names = self._ai_ops_property_names(scope)
        rows = []
        for prop_id in scope:
            total = rooms.get(prop_id, 0)
            if not total:
                warnings.append(
                    _("%s has no rooms for overnight stays.") % names.get(prop_id, "")
                )
                continue
            hot, cold = [], []
            for offset in range(days):
                day = date_from + timedelta(days=offset)
                counts = nights.get((prop_id, day), {"occupied": 0, "blocked": 0})
                occupied, blocked = counts["occupied"], counts["blocked"]
                pct = round(100.0 * occupied / total, 1)
                entry = {
                    "date": day,
                    "occupied": occupied,
                    "blocked": blocked,
                    "available": max(total - occupied - blocked, 0),
                    "occupancy_pct": pct,
                }
                overbooked = occupied + blocked > total
                if overbooked or pct >= hot_threshold_pct:
                    entry["overbooked_rooms"] = max(occupied + blocked - total, 0)
                    hot.append(entry)
                elif day <= cold_to and pct < cold_threshold_pct:
                    cold.append(entry)
            if hot or cold:
                rows.append(
                    {
                        "property_id": prop_id,
                        "property": names.get(prop_id, ""),
                        "rooms": total,
                        "hot_nights": len(hot),
                        "cold_nights": len(cold),
                        "hot": hot,
                        "cold": cold,
                    }
                )
        rows.sort(
            key=lambda row: (-row["hot_nights"], -row["cold_nights"], row["property"])
        )
        totals = {
            "properties_with_hot_nights": sum(1 for row in rows if row["hot_nights"]),
            "properties_with_cold_nights": sum(1 for row in rows if row["cold_nights"]),
            "hot_property_nights": sum(row["hot_nights"] for row in rows),
            "cold_property_nights": sum(row["cold_nights"] for row in rows),
        }
        return {
            "summary": _(
                "%(hot)s hot property nights (opportunity to raise prices) up to "
                "%(date_to)s and %(cold)s cold ones (low occupancy) up to %(cold_to)s."
            )
            % {
                "hot": totals["hot_property_nights"],
                "cold": totals["cold_property_nights"],
                "date_to": date_to,
                "cold_to": cold_to,
            },
            "period": {
                "date_from": date_from,
                "date_to": date_to,
                "cold_date_to": cold_to,
            },
            "definitions": [
                _(
                    "Occupancy: occupied nights (normal and staff reservations in "
                    "active rooms for overnight stays, without pre-reservations "
                    "nor cancelled ones) over the rooms of the property, as in "
                    "the occupancy outlook; blocked rooms are not taken out of "
                    "the denominator."
                ),
                _(
                    "Hot night: occupancy at the hot threshold (%(hot)s%%) or more, "
                    "or more rooms used (occupied plus blocked) than the property "
                    "has, which means overbooking: a chance to raise prices."
                )
                % {"hot": hot_threshold_pct},
                _(
                    "Cold night: occupancy below the cold threshold (%(cold)s%%) "
                    "in the first %(days)s days from today only."
                )
                % {"cold": cold_threshold_pct, "days": cold_horizon_days},
            ],
            "warnings": warnings,
            "result": {
                "totals": totals,
                "by_property": self._ai_limit_rows(rows, warnings),
            },
        }
