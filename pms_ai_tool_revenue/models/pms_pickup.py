from collections import defaultdict
from datetime import timedelta

from odoo import _, models
from odoo.exceptions import UserError

from odoo.addons.pms_ai_tool.tools import guarded_tool

from . import revenue_common as rc

MAX_BOOKING_DAYS = 31
DEFAULT_BOOKING_DAYS = 7

# Lines of the stay period of the reservations created in the booking window
# and of those cancelled in it, whenever they were created. A cancelled
# reservation keeps its price, zeroed only by the cancellation discount, so the
# price before the cancellation is taken from price and discount.
PICKUP_NIGHTS_CTE = """
nights AS (
    SELECT rl.pms_property_id AS property_id,
           to_char(rl.date, 'YYYY-MM') AS month,
           rl.reservation_id,
           r.folio_id,
           (r.create_date >= %(booked_start)s
            AND r.create_date < %(booked_end)s) AS created_in,
           (r.state = 'cancel') AS cancelled,
           coalesce(
               r.state = 'cancel'
               AND r.cancel_datetime >= %(booked_start)s
               AND r.cancel_datetime < %(booked_end)s,
               false
           ) AS cancelled_in,
           coalesce(r.cancelled_reason = 'modified', false) AS modified,
           coalesce(
               r.state = 'cancel'
               AND r.cancel_datetime IS NULL
               AND r.write_date >= %(booked_start)s
               AND r.write_date < %(booked_end)s,
               false
           ) AS undated_recent,
           rl.price * (1 - coalesce(rl.discount, 0) / 100.0) AS gross
      FROM pms_reservation_line rl
      JOIN pms_reservation r ON r.id = rl.reservation_id
     WHERE rl.date BETWEEN %(date_from)s AND %(date_to)s
       AND rl.pms_property_id = ANY(%(property_ids)s)
       AND r.reservation_type = 'normal'
       AND coalesce(rl.overnight_room, true)
       AND NOT coalesce(rl.is_reselling, false)
       AND (
            (r.create_date >= %(booked_start)s AND r.create_date < %(booked_end)s)
            OR (
                r.state = 'cancel'
                AND (
                    (r.cancel_datetime >= %(booked_start)s
                     AND r.cancel_datetime < %(booked_end)s)
                    OR (r.cancel_datetime IS NULL
                        AND r.write_date >= %(booked_start)s
                        AND r.write_date < %(booked_end)s)
                )
            )
       )
)"""

SIDE_SETS = {
    "created_cancelled": "n.created_in AND n.cancelled",
    "cancelled": "n.cancelled_in",
    "cancelled_before": "n.cancelled_in AND NOT n.created_in",
    "modified": "n.cancelled_in AND n.modified",
}

PICKUP_QUERY = (
    "WITH "
    + PICKUP_NIGHTS_CTE
    + ",\n"
    + rc.TAXES_CTE
    + ",\n"
    + rc.ZERO_FOLIOS_CTE
    + "\nSELECT n.property_id, n.month,"
    + rc.measure_columns("n.created_in AND NOT n.cancelled", "_booked")
    + ","
    + ",".join(rc.volume_columns(cond, "_" + name) for name, cond in SIDE_SETS.items())
    + ",\n       count(*) FILTER (WHERE n.undated_recent AND NOT n.created_in)"
    + " AS undated_nights"
    + rc.FROM_NIGHTS
    + " GROUP BY n.property_id, n.month"
)


def _new_cell():
    return dict(
        {"booked": rc.new_counters(), "undated": 0},
        **{name: {"nights": 0, "net": 0.0} for name in SIDE_SETS},
    )


def _add_row(cell, row):
    rc.add_counters(cell["booked"], row, "_booked")
    cell["undated"] += row["undated_nights"]
    for name in SIDE_SETS:
        cell[name]["nights"] += row["nights_" + name]
        cell[name]["net"] += float(row["net_" + name] or 0)


def _merge(target, source):
    rc.merge_counters(target["booked"], source["booked"])
    target["undated"] += source["undated"]
    for name in SIDE_SETS:
        target[name]["nights"] += source[name]["nights"]
        target[name]["net"] += source[name]["net"]


def _figures(cell):
    booked = cell["booked"]
    earlier = cell["cancelled_before"]
    return {
        "booked_nights": booked["nights"],
        "booked_revenue": rc.money(booked["net"]),
        "booked_adr": rc.adr(booked),
        "booked_then_cancelled_nights": cell["created_cancelled"]["nights"],
        "cancelled_nights": cell["cancelled"]["nights"],
        "cancelled_revenue": rc.money(cell["cancelled"]["net"]),
        "cancelled_modified_nights": cell["modified"]["nights"],
        "net_nights": booked["nights"] - earlier["nights"],
        "net_revenue": rc.money(booked["net"] - earlier["net"]),
    }


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    def _ai_rev_booking_window(self, booked_from, booked_to):
        """Booking window: the last 7 days unless given, at most 31 days."""
        today = self._ai_rev_now()[0].date()
        step = timedelta(days=DEFAULT_BOOKING_DAYS - 1)
        if booked_from and not booked_to:
            booked_to = booked_from + step
        booked_to = booked_to or today
        booked_from = booked_from or booked_to - step
        if booked_to < booked_from:
            raise UserError(
                _("The end of the booking window is earlier than its start.")
            )
        if (booked_to - booked_from).days + 1 > MAX_BOOKING_DAYS:
            raise UserError(
                _("The booking window cannot be longer than %s days.")
                % MAX_BOOKING_DAYS
            )
        return booked_from, booked_to

    @guarded_tool(
        input_schema={
            "stay_from": rc.date_input("First night of the stays (YYYY-MM-DD)."),
            "stay_to": rc.date_input(
                "Last night of the stays, included (YYYY-MM-DD). "
                f"At most {rc.MAX_DAYS} days after stay_from."
            ),
            "booked_from": rc.date_input(
                "First day of the booking window (YYYY-MM-DD). Default: 6 days "
                "before booked_to."
            ),
            "booked_to": rc.date_input(
                "Last day of the booking window, included (YYYY-MM-DD). At most "
                f"{MAX_BOOKING_DAYS} days after booked_from. Default: today."
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
    def _ai_pms_pickup(
        self,
        stay_from,
        stay_to,
        booked_from=None,
        booked_to=None,
        property_ids=None,
    ):
        stay = self._ai_check_period(stay_from, stay_to, rc.MAX_DAYS)
        booked_from, booked_to = self._ai_rev_booking_window(booked_from, booked_to)
        property_ids = self._ai_scope_property_ids(property_ids)
        booked_start, booked_end = self._ai_utc_bounds(booked_from, booked_to)
        rows = self._ai_fetch(
            PICKUP_QUERY,
            {
                "date_from": stay_from,
                "date_to": stay_to,
                "property_ids": property_ids,
                "booked_start": booked_start,
                "booked_end": booked_end,
            },
        )
        total = _new_cell()
        by_month = defaultdict(_new_cell)
        by_property = defaultdict(_new_cell)
        by_property_month = defaultdict(lambda: defaultdict(_new_cell))
        for row in rows:
            _add_row(by_property_month[row["property_id"]][row["month"]], row)
        for property_id, months in by_property_month.items():
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
                    "by_month": [
                        {"month": month, **_figures(cells[month])}
                        for month in sorted(cells)
                    ],
                }
                for property_id, cell in by_property.items()
                for cells in (by_property_month[property_id],)
            ),
            key=lambda r: (-r["booked_revenue"], r["property"]),
        )

        totals = _figures(total)
        warnings = self._ai_rev_quality_warnings(total["booked"])
        if totals["cancelled_modified_nights"]:
            warnings.append(
                _(
                    "%s of the cancelled nights come from modified bookings: a new "
                    "reservation replaced them, so they are not lost business."
                )
                % totals["cancelled_modified_nights"]
            )
        if total["undated"]:
            warnings.append(
                _(
                    "%s nights belong to cancelled reservations with no cancel "
                    "date that were changed in the booking window: they may be "
                    "cancellations of the window that cannot be dated, so the "
                    "cancelled figures may be understated."
                )
                % total["undated"]
            )
        tz = self._ai_timezone()
        definitions = self._ai_rev_definitions()
        return {
            "summary": _(
                "%(nights)s room nights (%(revenue)s without VAT) booked from "
                "%(booked_from)s to %(booked_to)s for stays from %(from)s to "
                "%(to)s; %(cancelled)s nights cancelled in the window, net "
                "pickup %(net)s nights."
            )
            % {
                "nights": totals["booked_nights"],
                "revenue": totals["booked_revenue"],
                "booked_from": booked_from,
                "booked_to": booked_to,
                "from": stay_from,
                "to": stay_to,
                "cancelled": totals["cancelled_nights"],
                "net": totals["net_nights"],
            },
            "period": {
                "booked_from": booked_from,
                "booked_to": booked_to,
                "stay_from": stay["date_from"],
                "stay_to": stay["date_to"],
                "time_zone": tz,
            },
            "definitions": [
                _(
                    "Booked: room nights of the reservations created in the "
                    "booking window, dated in the stay period, that are still on "
                    "the books (normal reservations in rooms for overnight stays, "
                    "not cancelled nor resold; pre-reservations count). The "
                    "creation moment is the one the PMS records when the "
                    "reservation is created, taken in the time zone %s."
                )
                % tz,
                definitions["revenue"],
                _(
                    "Cancelled: room nights of the reservations cancelled in the "
                    "booking window, whenever they were booked, by the cancel "
                    "date the PMS records when a reservation is cancelled; their "
                    "revenue is the price before the cancellation, without VAT. "
                    "booked_then_cancelled_nights are those of reservations "
                    "created in the window and cancelled afterwards."
                ),
                _(
                    "cancelled_modified_nights: part of the cancelled nights that "
                    "is not lost business, because the booking was modified and a "
                    "new reservation replaced it (the new one counts as booked)."
                ),
                _(
                    "Net: booked minus the nights and revenue of reservations "
                    "booked before the window and cancelled in it."
                ),
                definitions["adr"],
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
