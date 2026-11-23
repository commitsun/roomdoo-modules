from collections import defaultdict
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import _, models

from odoo.addons.pms_ai_tool.tools import guarded_tool

from . import revenue_common as rc
from .pms_pickup import PICKUP_QUERY, _add_row, _figures, _merge, _new_cell

MAX_HORIZON_MONTHS = 12
DEFAULT_HORIZON_MONTHS = 6


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    @guarded_tool(
        input_schema={
            "booking_day": rc.date_input(
                "Booking day to analyse (YYYY-MM-DD). Default: yesterday."
            ),
            "horizon_months": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_HORIZON_MONTHS,
                "description": "Stay months ahead of the booking day, counting the "
                f"month of the booking day: 1 to {MAX_HORIZON_MONTHS} "
                f"({DEFAULT_HORIZON_MONTHS}).",
            },
            "min_net_nights": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100000,
                "description": "Only properties that lost at least this many net "
                "nights that day (1).",
            },
            "property_ids": rc.PROPERTY_IDS_INPUT,
        },
        output_schema={
            "totals": {"type": "object"},
            "by_property": {"type": "array", "items": {"type": "object"}},
        },
    )
    def _ai_pms_negative_pickup(
        self,
        booking_day=None,
        horizon_months=DEFAULT_HORIZON_MONTHS,
        min_net_nights=1,
        property_ids=None,
    ):
        today = self._ai_rev_now()[0].date()
        booking_day = booking_day or today - timedelta(days=1)
        stay_from = booking_day
        stay_to = (
            booking_day.replace(day=1)
            + relativedelta(months=horizon_months)
            - timedelta(days=1)
        )
        property_ids = self._ai_scope_property_ids(property_ids)
        booked_start, booked_end = self._ai_utc_bounds(booking_day, booking_day)
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
        by_property_month = defaultdict(lambda: defaultdict(_new_cell))
        for row in rows:
            _add_row(by_property_month[row["property_id"]][row["month"]], row)
        names = self._ai_rev_property_names(by_property_month)
        result_rows = []
        total = _new_cell()
        for property_id, months in by_property_month.items():
            cell = _new_cell()
            for month_cell in months.values():
                _merge(cell, month_cell)
            figures = _figures(cell)
            if figures["net_nights"] > -min_net_nights:
                continue
            _merge(total, cell)
            result_rows.append(
                {
                    "property_id": property_id,
                    "property": names.get(property_id, ""),
                    **figures,
                    "negative_months": [
                        {"month": month, **_figures(months[month])}
                        for month in sorted(months)
                        if _figures(months[month])["net_nights"] < 0
                    ],
                }
            )
        result_rows.sort(key=lambda r: (r["net_nights"], r["property"]))
        totals = _figures(total)
        totals["properties"] = len(result_rows)
        warnings = []
        if total["undated"]:
            warnings.append(
                _(
                    "%s nights belong to cancelled reservations with no cancel "
                    "date that were changed on the booking day: they may be "
                    "cancellations of that day that cannot be dated, so the "
                    "cancelled figures may be understated."
                )
                % total["undated"]
            )
        tz = self._ai_timezone()
        definitions = self._ai_rev_definitions()
        return {
            "summary": _(
                "%(count)s properties with negative net pickup on %(day)s: "
                "%(nights)s net nights (%(revenue)s without VAT) for stays from "
                "%(from)s to %(to)s."
            )
            % {
                "count": len(result_rows),
                "day": booking_day,
                "nights": totals["net_nights"],
                "revenue": totals["net_revenue"],
                "from": stay_from,
                "to": stay_to,
            },
            "period": {
                "booking_day": booking_day,
                "stay_from": stay_from,
                "stay_to": stay_to,
                "time_zone": tz,
            },
            "definitions": [
                _(
                    "Net pickup of a booking day: room nights of the reservations "
                    "created that day and still on the books, minus those of "
                    "reservations booked earlier and cancelled that day, as in "
                    "the pickup tool. Stays are the ones from the booking day to "
                    "the end of the horizon; the day is taken in the time zone "
                    "%s."
                )
                % tz,
                _(
                    "A property is listed when its net pickup is negative (at "
                    "least min_net_nights lost); negative_months are the stay "
                    "months where it is negative. Nights and revenue of the "
                    "cancelled ones are valued at the price before the "
                    "cancellation, without VAT."
                ),
                definitions["revenue"],
            ],
            "warnings": warnings,
            "result": {
                "totals": totals,
                "by_property": self._ai_limit_rows(result_rows, warnings),
            },
        }
