from collections import defaultdict
from datetime import date, timedelta

from odoo import _, models

from odoo.addons.pms_ai_tool.tools import guarded_tool

from . import revenue_common as rc
from .pms_revenue_kpis import _figures as kpi_figures
from .pms_revenue_kpis import _new_data, _sum_data

WINDOWS = ("day", "month_to_date", "year_to_date")

# Reservations of the window by property: arrivals (and the nights of their
# stays), cancellations dated in the window and new confirmed reservations.
RESERVATIONS_QUERY = """
SELECT r.pms_property_id AS property_id,
       count(*) FILTER (
           WHERE r.checkin BETWEEN %(date_from)s AND %(date_to)s
             AND r.state <> 'cancel'
       ) AS arrivals,
       coalesce(sum(r.checkout - r.checkin) FILTER (
           WHERE r.checkin BETWEEN %(date_from)s AND %(date_to)s
             AND r.state <> 'cancel'
       ), 0) AS stay_nights,
       count(*) FILTER (
           WHERE r.state = 'cancel'
             AND r.cancel_datetime >= %(start)s AND r.cancel_datetime < %(end)s
             AND coalesce(r.cancelled_reason, '') <> 'modified'
       ) AS cancelled,
       count(*) FILTER (
           WHERE r.create_date >= %(start)s AND r.create_date < %(end)s
             AND r.state NOT IN ('draft', 'cancel')
       ) AS confirmed
  FROM pms_reservation r
  JOIN pms_room_type rt ON rt.id = r.room_type_id
 WHERE r.pms_property_id = ANY(%(property_ids)s)
   AND r.reservation_type = 'normal'
   AND coalesce(rt.overnight_room, true)
   AND (
        r.checkin BETWEEN %(date_from)s AND %(date_to)s
        OR (r.cancel_datetime >= %(start)s AND r.cancel_datetime < %(end)s)
        OR (r.create_date >= %(start)s AND r.create_date < %(end)s)
   )
 GROUP BY r.pms_property_id
"""

# Services dated in the window. Food and beverage are the lines of board
# services or of products used in some board service; the rest are other
# services. Cancelled reservations carry no sale.
SERVICES_QUERY = """
SELECT sl.pms_property_id AS property_id,
       sl.product_id,
       (sl.is_board_service
        OR sl.product_id IN (SELECT product_id FROM pms_board_service_line)
       ) AS is_fnb,
       coalesce(sum(sl.price_day_subtotal), 0) AS net,
       coalesce(sum(sl.price_day_total), 0) AS gross
  FROM pms_service_line sl
  JOIN pms_reservation r ON r.id = sl.reservation_id
 WHERE sl.date BETWEEN %(date_from)s AND %(date_to)s
   AND sl.pms_property_id = ANY(%(property_ids)s)
   AND r.reservation_type = 'normal'
   AND r.state <> 'cancel'
 GROUP BY sl.pms_property_id, sl.product_id, 3
"""


def _new_cell():
    return {
        "kpi": _new_data(),
        "arrivals": 0,
        "stay_nights": 0,
        "cancelled": 0,
        "confirmed": 0,
        "fnb": defaultdict(lambda: [0.0, 0.0]),
        "other": [0.0, 0.0],
    }


def _merge(target, source):
    rc.merge_counters(target["kpi"], source["kpi"])
    for name in ("capacity", "out", "staff"):
        target["kpi"][name] += source["kpi"][name]
    for name in ("arrivals", "stay_nights", "cancelled", "confirmed"):
        target[name] += source[name]
    for product_id, amounts in source["fnb"].items():
        target["fnb"][product_id][0] += amounts[0]
        target["fnb"][product_id][1] += amounts[1]
    target["other"][0] += source["other"][0]
    target["other"][1] += source["other"][1]


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    def _ai_rev_daily_window(self, date_from, date_to, property_ids):
        """Raw figures of a window, by property."""
        cells = defaultdict(_new_cell)
        for property_id, data in self._ai_rev_kpi_period(
            date_from, date_to, property_ids
        ).items():
            cells[property_id]["kpi"] = data
        start, end = self._ai_utc_bounds(date_from, date_to)
        params = {
            "date_from": date_from,
            "date_to": date_to,
            "property_ids": property_ids,
            "start": start,
            "end": end,
        }
        for row in self._ai_fetch(RESERVATIONS_QUERY, params):
            cell = cells[row["property_id"]]
            cell["arrivals"] += row["arrivals"]
            cell["stay_nights"] += row["stay_nights"]
            cell["cancelled"] += row["cancelled"]
            cell["confirmed"] += row["confirmed"]
        for row in self._ai_fetch(SERVICES_QUERY, params):
            cell = cells[row["property_id"]]
            net, gross = float(row["net"]), float(row["gross"])
            target = cell["fnb"][row["product_id"]] if row["is_fnb"] else cell["other"]
            target[0] += net
            target[1] += gross
        return cells

    def _ai_rev_daily_figures(self, cell, product_names):
        figures = kpi_figures(cell["kpi"])
        counters = cell["kpi"]
        fnb = [
            {
                "service": product_names.get(product_id, ""),
                "revenue": rc.money(amounts[0]),
                "revenue_incl_vat": rc.money(amounts[1]),
            }
            for product_id, amounts in cell["fnb"].items()
        ]
        fnb.sort(key=lambda item: (-item["revenue"], item["service"]))
        return {
            "net_adr": figures["adr"],
            "gross_adr": round(counters["gross"] / counters["adr_nights"], 2)
            if counters["adr_nights"]
            else None,
            "occupancy_pct": figures["occupancy_pct"],
            "revpar": figures["revpar"],
            "rooms_sold": figures["room_nights_sold"],
            "rooms_blocked": figures["out_of_service_nights"],
            "average_length_of_stay": round(cell["stay_nights"] / cell["arrivals"], 2)
            if cell["arrivals"]
            else None,
            "cancelled_reservations": cell["cancelled"],
            "confirmed_reservations": cell["confirmed"],
            "room_revenue": figures["room_revenue"],
            "room_revenue_incl_vat": figures["room_revenue_incl_vat"],
            "fnb_revenue": rc.money(sum(a[0] for a in cell["fnb"].values())),
            "fnb_revenue_by_service": fnb,
            "other_services_revenue": rc.money(cell["other"][0]),
        }

    @guarded_tool(
        input_schema={
            "report_day": rc.date_input(
                "Day of the report (YYYY-MM-DD). Default: yesterday."
            ),
            "property_ids": rc.PROPERTY_IDS_INPUT,
        },
        output_schema={
            "totals": {"type": "object"},
            "by_property": {"type": "array", "items": {"type": "object"}},
        },
    )
    def _ai_pms_daily_management_report(self, report_day=None, property_ids=None):
        today = self._ai_rev_now()[0].date()
        report_day = report_day or today - timedelta(days=1)
        property_ids = self._ai_scope_property_ids(property_ids)
        windows = {
            "day": (report_day, report_day),
            "month_to_date": (report_day.replace(day=1), report_day),
            "year_to_date": (date(report_day.year, 1, 1), report_day),
        }
        raw = {
            name: self._ai_rev_daily_window(start, end, property_ids)
            for name, (start, end) in windows.items()
        }
        product_ids = {
            pid
            for cells in raw.values()
            for cell in cells.values()
            for pid in cell["fnb"]
        }
        product_names = {
            product.id: product.name
            for product in self.env["product.product"]
            .sudo()
            .with_context(active_test=False)
            .browse(list(product_ids))
        }
        names = self._ai_rev_property_names(property_ids)
        rows = []
        for property_id in property_ids:
            if not any(
                property_id in raw[name]
                and (
                    raw[name][property_id]["kpi"]["capacity"]
                    or raw[name][property_id]["kpi"]["nights"]
                )
                for name in WINDOWS
            ):
                continue
            rows.append(
                {
                    "property_id": property_id,
                    "property": names.get(property_id, ""),
                    **{
                        name: self._ai_rev_daily_figures(
                            raw[name][property_id], product_names
                        )
                        for name in WINDOWS
                    },
                }
            )
        rows.sort(key=lambda row: row["property"])
        totals = {}
        for name in WINDOWS:
            total = _new_cell()
            for row in rows:
                _merge(total, raw[name][row["property_id"]])
            totals[name] = self._ai_rev_daily_figures(total, product_names)
        warnings = self._ai_rev_quality_warnings(
            _sum_data(
                [cell["kpi"] for cells in raw.values() for cell in cells.values()]
            )
        )
        definitions = self._ai_rev_definitions()
        day_figures = totals["day"]
        return {
            "summary": _(
                "Day %(day)s: occupancy %(occupancy)s%%, net ADR %(adr)s, RevPAR "
                "%(revpar)s, %(sold)s rooms sold."
            )
            % {
                "day": report_day,
                "occupancy": _show(day_figures["occupancy_pct"]),
                "adr": _show(day_figures["net_adr"]),
                "revpar": _show(day_figures["revpar"]),
                "sold": day_figures["rooms_sold"],
            },
            "period": {
                "report_day": report_day,
                "month_to_date": {"date_from": windows["month_to_date"][0]},
                "year_to_date": {"date_from": windows["year_to_date"][0]},
                "time_zone": self._ai_timezone(),
            },
            "definitions": [
                definitions["nights"],
                _(
                    "Columns: the day, the month to date and the year to date, "
                    "all of them ending on the report day. Rooms sold are room "
                    "nights; rooms blocked are nights of out of service blocks."
                ),
                _(
                    "Occupancy: rooms sold / room nights available (capacity "
                    "minus out of service and staff nights). RevPAR: room revenue "
                    "without VAT / room nights available."
                ),
                definitions["revenue"],
                _(
                    "Net ADR: room revenue without VAT / room nights, leaving out "
                    "the nights of folios worth 0. Gross ADR: the same with the "
                    "final prices as the PMS stores them (VAT included when prices "
                    "are set with taxes included)."
                ),
                _(
                    "Average length of stay: nights of the stays that start in "
                    "the window (not cancelled) / number of those stays."
                ),
                _(
                    "Cancelled reservations: normal reservations cancelled in the "
                    "window (by the cancel date the PMS records), leaving out the "
                    "ones cancelled only because the booking was modified. "
                    "Confirmed reservations: normal reservations created in the "
                    "window that are neither pre-reservations nor cancelled."
                ),
                _(
                    "Food and beverage: service lines dated in the window of "
                    "board services, or of products used in some board service of "
                    "the PMS, by product (breakfast, lunch, dinner...), without "
                    "VAT (revenue_incl_vat with it). Other services: the rest of "
                    "the service lines. Cancelled reservations carry no revenue. "
                    "Drinks or meals sold as extras with a product that no board "
                    "service uses are counted as other services."
                ),
            ],
            "warnings": warnings,
            "result": {
                "totals": totals,
                "by_property": self._ai_limit_rows(rows, warnings),
            },
        }


def _show(value):
    return "-" if value is None else value
