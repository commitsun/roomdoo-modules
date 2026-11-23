from datetime import timedelta

from odoo import _, models

from odoo.addons.pms_ai_tool.tools import guarded_tool

from . import revenue_common as rc
from .pms_channel_mix import _changes, _figures, _sum

MAX_OUTLOOK_DAYS = 93
DEFAULT_OUTLOOK_DAYS = 90
MAX_TOP = 30
DEFAULT_TOP = 15

# Last year's nights as they were on the books at the same moment: created by
# then and not cancelled by then (the same rule as the pace tool), with the
# channel and the agency to split them.
LAST_YEAR_ASOF_CTE = """
nights AS (
    SELECT rl.pms_property_id AS property_id,
           rl.date,
           rl.reservation_id,
           r.folio_id,
           r.agency_id,
           COALESCE(rl.sale_channel_id, r.sale_channel_origin_id) AS channel_id,
           rl.price * (1 - coalesce(rl.discount, 0) / 100.0) AS gross
      FROM pms_reservation_line rl
      JOIN pms_reservation r ON r.id = rl.reservation_id
     WHERE rl.date BETWEEN %(date_from)s AND %(date_to)s
       AND rl.pms_property_id = ANY(%(property_ids)s)
       AND r.reservation_type = 'normal'
       AND coalesce(rl.overnight_room, true)
       AND NOT coalesce(rl.is_reselling, false)
       AND r.create_date < %(as_of)s
       AND (r.state <> 'cancel' OR r.cancel_datetime > %(as_of)s)
)"""

LAST_YEAR_CHANNEL_QUERY = (
    "WITH "
    + LAST_YEAR_ASOF_CTE
    + ",\n"
    + rc.TAXES_CTE
    + ",\n"
    + rc.ZERO_FOLIOS_CTE
    + "\nSELECT n.channel_id AS key_id,"
    + rc.measure_columns()
    + rc.FROM_NIGHTS
    + " GROUP BY 1"
)

LAST_YEAR_AGENCY_QUERY = (
    "WITH "
    + LAST_YEAR_ASOF_CTE
    + ",\n"
    + rc.TAXES_CTE
    + ",\n"
    + rc.ZERO_FOLIOS_CTE
    + "\nSELECT coalesce(cp.id, a.id) AS key_id,"
    + rc.measure_columns()
    + rc.FROM_NIGHTS
    + "  LEFT JOIN res_partner a ON a.id = n.agency_id\n"
    + "  LEFT JOIN res_partner cp ON cp.id = a.commercial_partner_id\n"
    + " GROUP BY 1"
)


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    def _ai_rev_outlook_last_year(self, group_by, params):
        query = (
            LAST_YEAR_AGENCY_QUERY if group_by == "agency" else LAST_YEAR_CHANNEL_QUERY
        )
        data = {}
        for row in self._ai_fetch(query, params):
            counters = rc.new_counters()
            rc.add_counters(counters, row)
            data[row["key_id"]] = counters
        return data

    def _ai_rev_outlook_rows(self, group_by, current, previous, top):
        total_now, total_before = _sum(current.values()), _sum(previous.values())
        empty = rc.new_counters()
        keys = sorted(
            set(current) | set(previous),
            key=lambda key: (
                -(current.get(key) or empty)["net"],
                -(previous.get(key) or empty)["net"],
                key or 0,
            ),
        )
        labels = self._ai_rev_mix_labels(group_by, keys)
        missing = (
            _("No agency (direct sales)") if group_by == "agency" else _("No channel")
        )
        rows = []
        for key in keys[:top]:
            name, kind = labels.get(key, (missing, None))
            now = _figures(current.get(key) or empty, total_now)
            before = _figures(previous.get(key) or empty, total_before)
            row = {"id": key, "name": name}
            if kind is not None:
                row["channel_type"] = kind
            rows.append(
                dict(
                    row,
                    **now,
                    last_year_same_time=before,
                    change_vs_last_year=_changes(now, before),
                )
            )
        rest = keys[top:]
        if rest:
            now = _figures(_sum(current.get(k) or empty for k in rest), total_now)
            before = _figures(
                _sum(previous.get(k) or empty for k in rest), total_before
            )
            rows.append(
                {
                    "id": None,
                    "name": _("Other (%s more)") % len(rest),
                    "is_other": True,
                    **now,
                    "last_year_same_time": before,
                    "change_vs_last_year": _changes(now, before),
                }
            )
        return rows, total_now, total_before

    @guarded_tool(
        input_schema={
            "days": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_OUTLOOK_DAYS,
                "description": f"Nights ahead from today, 1 to {MAX_OUTLOOK_DAYS} "
                f"({DEFAULT_OUTLOOK_DAYS}).",
            },
            "top": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_TOP,
                "description": f"Rows listed per split, by revenue ({DEFAULT_TOP}); "
                "the rest is added up in an 'other' row.",
            },
            "property_ids": rc.PROPERTY_IDS_INPUT,
        },
        output_schema={
            "totals": {"type": "object"},
            "by_channel": {"type": "array", "items": {"type": "object"}},
            "by_agency": {"type": "array", "items": {"type": "object"}},
        },
    )
    def _ai_pms_channel_agency_outlook(
        self, days=DEFAULT_OUTLOOK_DAYS, top=DEFAULT_TOP, property_ids=None
    ):
        local_now, _utc_now = self._ai_rev_now()
        date_from = local_now.date()
        date_to = date_from + timedelta(days=days - 1)
        period = self._ai_check_period(date_from, date_to, MAX_OUTLOOK_DAYS)
        property_ids = self._ai_scope_property_ids(property_ids)
        ly_from, ly_to = rc.last_year(date_from), rc.last_year(date_to)
        _local_before, as_of = self._ai_rev_a_year_ago(local_now)
        params_ly = {
            "date_from": ly_from,
            "date_to": ly_to,
            "property_ids": property_ids,
            "as_of": as_of,
        }
        result = {}
        totals = None
        for group_by in ("channel", "agency"):
            current = self._ai_rev_mix_period(
                group_by, date_from, date_to, property_ids
            )
            previous = self._ai_rev_outlook_last_year(group_by, params_ly)
            rows, total_now, total_before = self._ai_rev_outlook_rows(
                group_by, current, previous, top
            )
            result["by_" + group_by] = rows
            totals = totals or (total_now, total_before)
        total_now, total_before = totals
        definitions = self._ai_rev_definitions()
        tz = self._ai_timezone()
        return {
            "summary": _(
                "%(nights)s room nights (%(revenue)s without VAT) on the books "
                "from %(from)s to %(to)s, against %(ly_nights)s nights "
                "(%(ly_revenue)s) a year ago at this time."
            )
            % {
                "nights": total_now["nights"],
                "revenue": rc.money(total_now["net"]),
                "from": date_from,
                "to": date_to,
                "ly_nights": total_before["nights"],
                "ly_revenue": rc.money(total_before["net"]),
            },
            "period": dict(
                period,
                days=days,
                time_zone=tz,
                last_year={"date_from": ly_from, "date_to": ly_to},
            ),
            "definitions": [
                definitions["nights"],
                definitions["revenue"],
                definitions["adr"],
                _(
                    "Channel: the sale channel of each night (the one of the "
                    "reservation line or, if it has none, the origin channel of "
                    "the reservation). Agency: the agency company of the folio; "
                    "nights without agency are direct sales."
                ),
                _(
                    "Share: part of all the room nights and room revenue of the "
                    "period; changes in share are in percentage points."
                ),
                _(
                    "Last year at the same time: the same calendar dates one year "
                    "earlier, counting only the reservations created and not yet "
                    "cancelled at this same moment a year ago. Prices are the "
                    "ones recorded today, so later price changes also show."
                ),
            ],
            "warnings": self._ai_rev_quality_warnings(_sum([total_now, total_before])),
            "result": dict(
                {
                    "totals": {
                        "room_nights": total_now["nights"],
                        "room_revenue": rc.money(total_now["net"]),
                        "adr": rc.adr(total_now),
                        "last_year_same_time": {
                            "room_nights": total_before["nights"],
                            "room_revenue": rc.money(total_before["net"]),
                            "adr": rc.adr(total_before),
                        },
                    }
                },
                **result,
            ),
        }
