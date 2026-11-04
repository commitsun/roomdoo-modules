from collections import defaultdict

from odoo import _, models

from odoo.addons.pms_ai_tool.tools import guarded_tool

from . import revenue_common as rc

NAMES_SHOWN = 5

SOLD_QUERY = (
    "WITH "
    + rc.ALL_CTES
    + "\nSELECT n.property_id,"
    + rc.measure_columns()
    + rc.FROM_NIGHTS
    + " GROUP BY n.property_id"
)

CHANGED_FIGURES = (
    "room_nights_sold",
    "room_nights_available",
    "room_revenue",
    "adr",
    "revpar",
)


def _new_data():
    return dict(rc.new_counters(), capacity=0, out=0, staff=0)


def _sum_data(items):
    total = _new_data()
    for data in items:
        rc.merge_counters(total, data)
        for name in ("capacity", "out", "staff"):
            total[name] += data[name]
    return total


def _figures(data):
    sold = data["nights"]
    available = max(data["capacity"] - data["out"] - data["staff"], 0)
    return {
        "room_nights_sold": sold,
        "room_nights_capacity": data["capacity"],
        "out_of_service_nights": data["out"],
        "staff_nights": data["staff"],
        "room_nights_available": available,
        "occupancy_pct": rc.pct(sold, available),
        "room_revenue": rc.money(data["net"]),
        "room_revenue_incl_vat": rc.money(data["gross"]),
        "adr": rc.adr(data),
        "revpar": round(data["net"] / available, 2) if available else None,
        "zero_value_nights": data["zero_nights"],
    }


def _changes(current, last_year):
    changes = {key: rc.change(current[key], last_year[key]) for key in CHANGED_FIGURES}
    now, before = current["occupancy_pct"], last_year["occupancy_pct"]
    changes["occupancy_pct"] = {
        "change_points": round(now - before, 1)
        if now is not None and before is not None
        else None
    }
    return changes


def _show(value):
    return "-" if value is None else value


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    def _ai_rev_kpi_period(self, date_from, date_to, property_ids):
        """Sold nights, revenue, capacity and blocked nights per property."""
        params = {
            "date_from": date_from,
            "date_to": date_to,
            "property_ids": property_ids,
            "tz": self._ai_timezone(),
        }
        data = defaultdict(_new_data)
        for row in self._ai_fetch(SOLD_QUERY, params):
            rc.add_counters(data[row["property_id"]], row)
        for row in self._ai_fetch(rc.CAPACITY_QUERY, params):
            data[row["property_id"]]["capacity"] += row["room_nights"] or 0
        for row in self._ai_fetch(rc.BLOCKED_QUERY, params):
            data[row["property_id"]]["out"] += row["out_nights"]
            data[row["property_id"]]["staff"] += row["staff_nights"]
        return data

    def _ai_rev_kpi_rows(self, current, previous, compare):
        names = self._ai_rev_property_names(set(current) | set(previous))
        rows = []
        for property_id in set(current) | set(previous):
            now = current.get(property_id) or _new_data()
            before = previous.get(property_id) or _new_data()
            if not any(
                data[key] for data in (now, before) for key in ("capacity", "nights")
            ):
                continue
            row = {
                "property_id": property_id,
                "property": names.get(property_id, ""),
                **_figures(now),
            }
            if compare:
                row["last_year"] = _figures(before)
                row["vs_last_year"] = _changes(row, row["last_year"])
            rows.append(row)
        rows.sort(key=lambda r: (-r["room_revenue"], r["property"]))
        return rows

    def _ai_rev_kpi_warnings(self, rows, current, previous):
        warnings = self._ai_rev_quality_warnings(
            _sum_data(list(current.values()) + list(previous.values()))
        )
        crowded = [
            row["property"]
            for row in rows
            if row["room_nights_sold"]
            and (row["occupancy_pct"] is None or row["occupancy_pct"] > 100)
        ]
        if crowded:
            warnings.append(
                _(
                    "Occupancy is above 100%% or there are no room nights "
                    "available in %(count)s properties (%(names)s): overbooking, "
                    "or rooms missing from the capacity."
                )
                % {
                    "count": len(crowded),
                    "names": ", ".join(crowded[:NAMES_SHOWN]),
                }
            )
        return warnings

    @guarded_tool(
        input_schema={
            "date_from": rc.date_input("First night of the period (YYYY-MM-DD)."),
            "date_to": rc.date_input(
                "Last night of the period, included (YYYY-MM-DD). "
                f"At most {rc.MAX_DAYS} days after date_from."
            ),
            "property_ids": rc.PROPERTY_IDS_INPUT,
            "compare_last_year": {
                "type": "boolean",
                "description": "Compare with the same dates one year earlier "
                "(true by default).",
            },
        },
        output_schema={
            "totals": {"type": "object"},
            "by_property": {"type": "array", "items": {"type": "object"}},
        },
        required_inputs=["date_from", "date_to"],
    )
    def _ai_pms_revenue_kpis(
        self, date_from, date_to, property_ids=None, compare_last_year=True
    ):
        period = self._ai_check_period(date_from, date_to, rc.MAX_DAYS)
        property_ids = self._ai_scope_property_ids(property_ids)
        ly_from, ly_to = rc.last_year(date_from), rc.last_year(date_to)
        current = self._ai_rev_kpi_period(date_from, date_to, property_ids)
        previous = (
            self._ai_rev_kpi_period(ly_from, ly_to, property_ids)
            if compare_last_year
            else {}
        )
        rows = self._ai_rev_kpi_rows(current, previous, compare_last_year)
        warnings = self._ai_rev_kpi_warnings(rows, current, previous)

        totals = _figures(_sum_data(current.values()))
        summary = _(
            "Occupancy %(occupancy)s%% (%(sold)s of %(available)s room nights), "
            "ADR %(adr)s, RevPAR %(revpar)s, from %(from)s to %(to)s."
        ) % {
            "occupancy": _show(totals["occupancy_pct"]),
            "sold": totals["room_nights_sold"],
            "available": totals["room_nights_available"],
            "adr": _show(totals["adr"]),
            "revpar": _show(totals["revpar"]),
            "from": date_from,
            "to": date_to,
        }
        if compare_last_year:
            totals["last_year"] = _figures(_sum_data(previous.values()))
            totals["vs_last_year"] = _changes(totals, totals["last_year"])
            before = totals["last_year"]
            summary += " " + _(
                "Last year: occupancy %(occupancy)s%%, ADR %(adr)s, RevPAR %(revpar)s."
            ) % {
                "occupancy": _show(before["occupancy_pct"]),
                "adr": _show(before["adr"]),
                "revpar": _show(before["revpar"]),
            }
            period["last_year"] = {"date_from": ly_from, "date_to": ly_to}

        definitions = self._ai_rev_definitions()
        return {
            "summary": summary,
            "period": dict(period, days=(date_to - date_from).days + 1),
            "definitions": [
                definitions["nights"],
                _(
                    "Room nights available: for each night, the rooms for "
                    "overnight stays of the property that existed that night, "
                    "minus the rooms out of service and those used by staff "
                    "(both are reported). A room counts from its creation date, "
                    "or from its first occupied night if earlier; archived rooms "
                    "count until their last occupied night, because the PMS "
                    "keeps no retirement date."
                ),
                _(
                    "Occupancy: room nights sold / room nights available. It can "
                    "exceed 100% because the PMS accepts overbooking."
                ),
                definitions["revenue"],
                definitions["adr"],
                _("RevPAR: room revenue without VAT / room nights available."),
                _(
                    "Last year: the same calendar dates one year earlier, "
                    "calculated the same way from today's data."
                ),
            ],
            "warnings": warnings,
            "result": {
                "totals": totals,
                "by_property": self._ai_limit_rows(rows, warnings),
            },
        }
