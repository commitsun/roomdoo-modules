from odoo import _, models

from odoo.addons.pms_ai_tool.tools import guarded_tool

from . import revenue_common as rc

MAX_TOP = 30
DEFAULT_TOP = 10

CHANNEL_QUERY = (
    "WITH "
    + rc.ALL_CTES
    + "\nSELECT n.channel_id AS key_id,"
    + rc.measure_columns()
    + rc.FROM_NIGHTS
    + " GROUP BY 1"
)

# Agencies are companies: the commercial partner groups their contacts.
AGENCY_QUERY = (
    "WITH "
    + rc.ALL_CTES
    + "\nSELECT coalesce(cp.id, a.id) AS key_id,"
    + rc.measure_columns()
    + rc.FROM_NIGHTS
    + "  LEFT JOIN res_partner a ON a.id = n.agency_id\n"
    + "  LEFT JOIN res_partner cp ON cp.id = a.commercial_partner_id\n"
    + " GROUP BY 1"
)


def _figures(counters, totals):
    return {
        "room_nights": counters["nights"],
        "room_nights_share_pct": rc.pct(counters["nights"], totals["nights"]),
        "room_revenue": rc.money(counters["net"]),
        "room_revenue_share_pct": rc.pct(counters["net"], totals["net"]),
        "adr": rc.adr(counters),
    }


def _points(now, before, key):
    if now[key] is None or before[key] is None:
        return None
    return round(now[key] - before[key], 1)


def _changes(now, before):
    return {
        "room_nights": rc.change(now["room_nights"], before["room_nights"]),
        "room_revenue": rc.change(now["room_revenue"], before["room_revenue"]),
        "room_nights_share_points": _points(now, before, "room_nights_share_pct"),
        "room_revenue_share_points": _points(now, before, "room_revenue_share_pct"),
    }


def _sum(items):
    total = rc.new_counters()
    for counters in items:
        rc.merge_counters(total, counters)
    return total


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    def _ai_rev_mix_period(self, group_by, date_from, date_to, property_ids):
        """Counters per channel or agency (None: no channel or no agency)."""
        query = AGENCY_QUERY if group_by == "agency" else CHANNEL_QUERY
        data = {}
        for row in self._ai_fetch(
            query,
            {
                "date_from": date_from,
                "date_to": date_to,
                "property_ids": property_ids,
            },
        ):
            counters = rc.new_counters()
            rc.add_counters(counters, row)
            data[row["key_id"]] = counters
        return data

    def _ai_rev_mix_labels(self, group_by, keys):
        """Name and type of each channel or agency (names only, no guest data)."""
        ids = [key for key in keys if key]
        if group_by == "agency":
            partners = self.env["res.partner"].sudo().with_context(active_test=False)
            return {p.id: (p.name, None) for p in partners.browse(ids)}
        channels = self.env["pms.sale.channel"].sudo().with_context(active_test=False)
        return {c.id: (c.name, c.channel_type or "") for c in channels.browse(ids)}

    @guarded_tool(
        input_schema={
            "date_from": rc.date_input("First night of the period (YYYY-MM-DD)."),
            "date_to": rc.date_input(
                "Last night of the period, included (YYYY-MM-DD). "
                f"At most {rc.MAX_DAYS} days after date_from."
            ),
            "property_ids": rc.PROPERTY_IDS_INPUT,
            "group_by": {
                "type": "string",
                "enum": ["channel", "agency"],
                "description": "Split by sale channel or by agency (channel).",
            },
            "top": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_TOP,
                "description": f"Rows listed, by revenue ({DEFAULT_TOP}); the rest "
                "is added up in an 'other' row.",
            },
        },
        output_schema={
            "totals": {"type": "object"},
            "rows": {"type": "array", "items": {"type": "object"}},
        },
        required_inputs=["date_from", "date_to"],
    )
    def _ai_pms_channel_mix(
        self, date_from, date_to, property_ids=None, group_by="channel", top=DEFAULT_TOP
    ):
        period = self._ai_check_period(date_from, date_to, rc.MAX_DAYS)
        property_ids = self._ai_scope_property_ids(property_ids)
        ly_from, ly_to = rc.last_year(date_from), rc.last_year(date_to)
        current = self._ai_rev_mix_period(group_by, date_from, date_to, property_ids)
        previous = self._ai_rev_mix_period(group_by, ly_from, ly_to, property_ids)
        total_now, total_before = _sum(current.values()), _sum(previous.values())

        keys = sorted(
            set(current) | set(previous),
            key=lambda key: (
                -(current.get(key) or rc.new_counters())["net"],
                -(previous.get(key) or rc.new_counters())["net"],
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
            now = _figures(current.get(key) or rc.new_counters(), total_now)
            before = _figures(previous.get(key) or rc.new_counters(), total_before)
            row = {"id": key, "name": name}
            if kind is not None:
                row["channel_type"] = kind
            rows.append(
                dict(
                    row,
                    **now,
                    last_year=before,
                    change_vs_last_year=_changes(now, before),
                )
            )
        rest = keys[top:]
        if rest:
            now = _figures(
                _sum(current.get(k) or rc.new_counters() for k in rest), total_now
            )
            before = _figures(
                _sum(previous.get(k) or rc.new_counters() for k in rest), total_before
            )
            rows.append(
                {
                    "id": None,
                    "name": _("Other (%s more)") % len(rest),
                    "is_other": True,
                    **now,
                    "last_year": before,
                    "change_vs_last_year": _changes(now, before),
                }
            )

        merged = _sum([total_now, total_before])
        definitions = self._ai_rev_definitions()
        if group_by == "agency":
            split = _(
                "Agency: the agency company of the folio (its contacts are "
                "grouped under the company); nights without agency are grouped "
                "as direct sales."
            )
            group_label = _("agency")
        else:
            split = _(
                "Channel: the sale channel of each night (the one of the "
                "reservation line or, if it has none, the origin channel of the "
                "reservation)."
            )
            group_label = _("sale channel")
        leader = rows[0] if rows else None
        return {
            "summary": _(
                "Mix of %(nights)s room nights by %(group)s from %(from)s to "
                "%(to)s; the first one is %(name)s with %(share)s%% of the "
                "revenue."
            )
            % {
                "nights": total_now["nights"],
                "group": group_label,
                "from": date_from,
                "to": date_to,
                "name": leader["name"] if leader else "-",
                "share": leader["room_revenue_share_pct"] if leader else "-",
            },
            "period": dict(
                period,
                days=(date_to - date_from).days + 1,
                last_year={"date_from": ly_from, "date_to": ly_to},
            ),
            "definitions": [
                definitions["nights"],
                split,
                definitions["revenue"],
                definitions["adr"],
                _(
                    "Share: part of all the room nights and of all the room "
                    "revenue without VAT of the period; the other row adds up the "
                    "rest. Changes in share are in percentage points."
                ),
                _(
                    "Last year: the same calendar dates one year earlier, "
                    "calculated the same way from today's data."
                ),
            ],
            "warnings": self._ai_rev_quality_warnings(merged),
            "result": {
                "totals": {
                    "room_nights": total_now["nights"],
                    "room_revenue": rc.money(total_now["net"]),
                    "adr": rc.adr(total_now),
                    "last_year": {
                        "room_nights": total_before["nights"],
                        "room_revenue": rc.money(total_before["net"]),
                        "adr": rc.adr(total_before),
                    },
                },
                "rows": rows,
            },
        }
