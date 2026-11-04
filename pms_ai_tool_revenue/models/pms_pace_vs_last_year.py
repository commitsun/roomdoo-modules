from collections import defaultdict

from odoo import _, models

from odoo.addons.pms_ai_tool.tools import guarded_tool

from . import revenue_common as rc

NOW_QUERY = (
    "WITH "
    + rc.ALL_CTES
    + "\nSELECT n.property_id, n.month,"
    + rc.measure_columns()
    + rc.FROM_NIGHTS
    + " GROUP BY n.property_id, n.month"
)

# Last year's stay dates, aligned to this year's months. A night was on the
# books at the cut-off if its reservation had been created by then and was not
# cancelled by then. A cancelled reservation keeps its price, zeroed only by the
# cancellation discount, so the price comes from price and discount.
LAST_YEAR_NIGHTS_CTE = """
nights AS (
    SELECT rl.pms_property_id AS property_id,
           to_char(rl.date + interval '1 year', 'YYYY-MM') AS month,
           rl.reservation_id,
           r.folio_id,
           rl.occupies_availability AS final_flag,
           (r.create_date < %(as_of)s) AS created_before,
           coalesce(
               r.create_date < %(as_of)s
               AND NOT coalesce(rl.is_reselling, false)
               AND (r.state <> 'cancel' OR r.cancel_datetime > %(as_of)s),
               false
           ) AS asof_flag,
           (r.state = 'cancel' AND r.cancel_datetime IS NULL) AS undated,
           rl.price * (1 - coalesce(rl.discount, 0) / 100.0) AS gross
      FROM pms_reservation_line rl
      JOIN pms_reservation r ON r.id = rl.reservation_id
     WHERE rl.date BETWEEN %(date_from)s AND %(date_to)s
       AND rl.pms_property_id = ANY(%(property_ids)s)
       AND r.reservation_type = 'normal'
       AND coalesce(rl.overnight_room, true)
       AND NOT coalesce(rl.is_reselling, false)
       AND (rl.occupies_availability OR r.create_date < %(as_of)s)
)"""

LAST_YEAR_QUERY = (
    "WITH "
    + LAST_YEAR_NIGHTS_CTE
    + ",\n"
    + rc.TAXES_CTE
    + ",\n"
    + rc.ZERO_FOLIOS_CTE
    + "\nSELECT n.property_id, n.month,"
    + rc.measure_columns("n.asof_flag", "_asof")
    + ","
    + rc.measure_columns("n.final_flag", "_final")
    + ",\n       count(*) FILTER (WHERE n.undated AND n.created_before)"
    + " AS undated_nights"
    + rc.FROM_NIGHTS
    + " GROUP BY n.property_id, n.month"
)

PERIODS = ("now", "same_time", "final")


def _new_cell():
    return dict({name: rc.new_counters() for name in PERIODS}, undated=0)


def _merge(target, source):
    for name in PERIODS:
        rc.merge_counters(target[name], source[name])
    target["undated"] += source["undated"]


def _summary(counters):
    return {
        "room_nights": counters["nights"],
        "room_revenue": rc.money(counters["net"]),
        "adr": rc.adr(counters),
    }


def _figures(cell):
    now = _summary(cell["now"])
    same_time = _summary(cell["same_time"])
    return {
        "now": now,
        "last_year_same_time": same_time,
        "last_year_final": _summary(cell["final"]),
        "change_vs_last_year_same_time": {
            key: rc.change(now[key], same_time[key])
            for key in ("room_nights", "room_revenue", "adr")
        },
    }


def _show(value):
    return "-" if value is None else value


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    @guarded_tool(
        input_schema={
            "stay_from": rc.date_input("First night of the stays (YYYY-MM-DD)."),
            "stay_to": rc.date_input(
                "Last night of the stays, included (YYYY-MM-DD). "
                f"At most {rc.MAX_DAYS} days after stay_from."
            ),
            "property_ids": rc.PROPERTY_IDS_INPUT,
        },
        output_schema={
            "totals": {"type": "object"},
            "by_month": {"type": "array", "items": {"type": "object"}},
            "by_property": {"type": "array", "items": {"type": "object"}},
        },
        required_inputs=["stay_from", "stay_to"],
    )
    def _ai_pms_pace_vs_last_year(self, stay_from, stay_to, property_ids=None):
        period = self._ai_check_period(stay_from, stay_to, rc.MAX_DAYS)
        property_ids = self._ai_scope_property_ids(property_ids)
        ly_from, ly_to = rc.last_year(stay_from), rc.last_year(stay_to)
        local_now, _utc_now = self._ai_rev_now()
        local_before, as_of = self._ai_rev_a_year_ago(local_now)
        cells = defaultdict(lambda: defaultdict(_new_cell))
        for row in self._ai_fetch(
            NOW_QUERY,
            {
                "date_from": stay_from,
                "date_to": stay_to,
                "property_ids": property_ids,
            },
        ):
            rc.add_counters(cells[row["property_id"]][row["month"]]["now"], row)
        for row in self._ai_fetch(
            LAST_YEAR_QUERY,
            {
                "date_from": ly_from,
                "date_to": ly_to,
                "property_ids": property_ids,
                "as_of": as_of,
            },
        ):
            cell = cells[row["property_id"]][row["month"]]
            rc.add_counters(cell["same_time"], row, "_asof")
            rc.add_counters(cell["final"], row, "_final")
            cell["undated"] += row["undated_nights"]

        total = _new_cell()
        by_month = defaultdict(_new_cell)
        by_property = defaultdict(_new_cell)
        for property_id, months in cells.items():
            for month, cell in months.items():
                _merge(by_property[property_id], cell)
                _merge(by_month[month], cell)
                _merge(total, cell)
        names = self._ai_rev_property_names(by_property)
        property_rows = sorted(
            (
                {
                    "property_id": property_id,
                    "property": names.get(property_id, ""),
                    **_figures(cell),
                }
                for property_id, cell in by_property.items()
            ),
            key=lambda r: (-r["now"]["room_revenue"], r["property"]),
        )

        totals = _figures(total)
        merged = rc.new_counters()
        rc.merge_counters(merged, total["now"])
        rc.merge_counters(merged, total["same_time"])
        warnings = self._ai_rev_quality_warnings(merged)
        today = local_now.date()
        if stay_from < today:
            warnings.append(
                _(
                    "Stay dates before %s are already past: their figure now is "
                    "what was finally sold, while last year at the same time is "
                    "the picture of the books on that day. For those dates "
                    "compare with last_year_final."
                )
                % today
            )
        if total["undated"]:
            warnings.append(
                _(
                    "%s nights of last year belong to cancelled reservations "
                    "with no cancel date: they are treated as cancelled before "
                    "the cut-off, so last year at the same time may be "
                    "understated."
                )
                % total["undated"]
            )
        definitions = self._ai_rev_definitions()
        change = totals["change_vs_last_year_same_time"]["room_nights"]
        return {
            "summary": _(
                "On the books %(nights)s room nights (%(revenue)s without VAT) "
                "for stays from %(from)s to %(to)s; last year at the same time "
                "%(ly_nights)s (%(ly_revenue)s): %(change)s room nights "
                "(%(change_pct)s%%)."
            )
            % {
                "nights": totals["now"]["room_nights"],
                "revenue": totals["now"]["room_revenue"],
                "from": stay_from,
                "to": stay_to,
                "ly_nights": totals["last_year_same_time"]["room_nights"],
                "ly_revenue": totals["last_year_same_time"]["room_revenue"],
                "change": _show(change["change"]),
                "change_pct": _show(change["change_pct"]),
            },
            "period": dict(
                period,
                days=(stay_to - stay_from).days + 1,
                last_year={"date_from": ly_from, "date_to": ly_to},
                now=local_now.isoformat(timespec="minutes"),
                last_year_as_of=local_before.isoformat(timespec="minutes"),
            ),
            "definitions": [
                _(
                    "On the books now: room nights of normal reservations in "
                    "rooms for overnight stays, dated in the stay period, that "
                    "occupy availability (neither cancelled nor resold; "
                    "pre-reservations count)."
                ),
                definitions["revenue"],
                definitions["adr"],
                _(
                    "Last year at the same time: the same stay dates one year "
                    "earlier, counting the reservations created before this same "
                    "moment a year ago and not cancelled by then, according to "
                    "the creation and cancel dates the PMS records."
                ),
                _("Last year final: what the same stay dates of last year hold today."),
                _(
                    "Last year is rebuilt from today's data: changes made to a "
                    "reservation after that moment (dates, rooms, prices) show "
                    "their current version."
                ),
            ],
            "warnings": warnings,
            "result": {
                "totals": totals,
                "by_month": [
                    {"month": month, **_figures(by_month[month])}
                    for month in sorted(by_month)
                ],
                "by_property": self._ai_limit_rows(property_rows, warnings),
            },
        }
