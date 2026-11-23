from collections import defaultdict
from datetime import timedelta

from odoo import _, models

from ..tools import guarded_tool

MAX_DAYS = 31
HISTORY_PERIODS = 4
RISING_ROWS = 10

METRICS_SCHEMA = {
    "nights": {"type": "integer"},
    "discounted_nights": {"type": "integer"},
    "discounted_nights_pct": {"type": "number"},
    "nights_50_or_more": {"type": "integer"},
    "nights_100": {"type": "integer"},
    "discount_amount": {"type": "number"},
    "discount_amount_100": {"type": "number"},
    "avg_discount_pct": {"type": "number"},
}

DISCOUNTS_QUERY = """
    SELECT rl.pms_property_id AS property_id,
           (%(date_to)s::date - rl.date) / %(days)s AS period_index,
           rl.sale_channel_id AS channel_id,
           count(*) AS nights,
           count(*) FILTER (WHERE rl.discount > 0) AS discounted_nights,
           count(*) FILTER (WHERE rl.discount >= 50) AS nights_50_or_more,
           count(*) FILTER (WHERE rl.discount >= 100) AS nights_100,
           count(*) FILTER (WHERE rl.price = 0) AS zero_price_nights,
           coalesce(sum(rl.price - rl.price_day_total), 0) AS discount_amount,
           coalesce(
               sum(rl.price - rl.price_day_total) FILTER (WHERE rl.discount >= 100),
               0
           ) AS discount_amount_100,
           coalesce(sum(rl.discount) FILTER (WHERE rl.discount > 0), 0)
               AS discount_pct_sum
      FROM pms_reservation_line rl
      JOIN pms_reservation r ON r.id = rl.reservation_id
     WHERE rl.date BETWEEN %(scan_from)s AND %(date_to)s
       AND rl.pms_property_id = ANY(%(property_ids)s)
       AND rl.occupies_availability
       AND r.reservation_type = 'normal'
  GROUP BY 1, 2, 3
"""

COUNTERS = (
    "nights",
    "discounted_nights",
    "nights_50_or_more",
    "nights_100",
    "zero_price_nights",
    "discount_amount",
    "discount_amount_100",
    "discount_pct_sum",
)


def _add(target, row):
    for counter in COUNTERS:
        target[counter] += row[counter]


def _metrics(counters):
    nights = counters["nights"]
    discounted = counters["discounted_nights"]
    return {
        "nights": nights,
        "discounted_nights": discounted,
        "discounted_nights_pct": round(100.0 * discounted / nights, 1)
        if nights
        else 0.0,
        "nights_50_or_more": counters["nights_50_or_more"],
        "nights_100": counters["nights_100"],
        "discount_amount": round(float(counters["discount_amount"]), 2),
        "discount_amount_100": round(float(counters["discount_amount_100"]), 2),
        "avg_discount_pct": round(float(counters["discount_pct_sum"]) / discounted, 1)
        if discounted
        else 0.0,
    }


def _new_counters():
    return dict.fromkeys(COUNTERS, 0)


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    @guarded_tool(
        input_schema={
            "date_from": {
                "type": "string",
                "format": "date",
                "description": "First night of the period (YYYY-MM-DD).",
            },
            "date_to": {
                "type": "string",
                "format": "date",
                "description": "Last night of the period, included (YYYY-MM-DD). "
                f"At most {MAX_DAYS} days after date_from.",
            },
            "property_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "maxItems": 200,
                "description": "Properties to include. Default: all the user's.",
            },
            "top": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "description": "Properties listed, by discounted amount (15).",
            },
        },
        output_schema={
            "totals": {"type": "object", "properties": METRICS_SCHEMA},
            "by_channel": {"type": "array", "items": {"type": "object"}},
            "by_property": {"type": "array", "items": {"type": "object"}},
            "rising": {"type": "array", "items": {"type": "object"}},
        },
        required_inputs=["date_from", "date_to"],
    )
    def _ai_pms_discounts_summary(self, date_from, date_to, property_ids=None, top=15):
        period = self._ai_check_period(date_from, date_to, MAX_DAYS)
        property_ids = self._ai_scope_property_ids(property_ids)
        days = (date_to - date_from).days + 1
        history = [
            {
                "date_from": date_from - timedelta(days=days * index),
                "date_to": date_to - timedelta(days=days * index),
            }
            for index in range(1, HISTORY_PERIODS + 1)
        ]
        rows = self._ai_fetch(
            DISCOUNTS_QUERY,
            {
                "date_to": date_to,
                "days": days,
                "scan_from": history[-1]["date_from"],
                "property_ids": property_ids,
            },
        )
        totals = _new_counters()
        by_channel = defaultdict(_new_counters)
        by_property = defaultdict(_new_counters)
        property_history = defaultdict(lambda: defaultdict(_new_counters))
        for row in rows:
            if row["period_index"] == 0:
                _add(totals, row)
                _add(by_channel[row["channel_id"]], row)
                _add(by_property[row["property_id"]], row)
            else:
                _add(property_history[row["property_id"]][row["period_index"]], row)

        properties = self.env["pms.property"].browse(
            list(by_property) + list(property_history)
        )
        names = {prop.id: prop.name for prop in properties}
        property_rows = []
        for property_id, counters in by_property.items():
            metrics = _metrics(counters)
            past = [
                _metrics(property_history[property_id][index])
                for index in range(1, HISTORY_PERIODS + 1)
                if property_history[property_id][index]["nights"]
            ]
            history_pct = (
                round(sum(m["discounted_nights_pct"] for m in past) / len(past), 1)
                if past
                else None
            )
            property_rows.append(
                {
                    "property_id": property_id,
                    "property": names.get(property_id, ""),
                    **metrics,
                    "history_periods": len(past),
                    "history_discounted_nights_pct": history_pct,
                    "change_pct_points": round(
                        metrics["discounted_nights_pct"] - history_pct, 1
                    )
                    if history_pct is not None
                    else None,
                }
            )
        property_rows.sort(key=lambda r: r["discount_amount"], reverse=True)
        rising = sorted(
            (r for r in property_rows if (r["change_pct_points"] or 0) > 0),
            key=lambda r: r["change_pct_points"],
            reverse=True,
        )[:RISING_ROWS]

        channels = self.env["pms.sale.channel"].browse(
            [channel_id for channel_id in by_channel if channel_id]
        )
        channel_info = {
            channel.id: (channel.name, channel.channel_type or "")
            for channel in channels
        }
        channel_rows = sorted(
            (
                {
                    "channel": channel_info.get(channel_id, (_("No channel"), ""))[0],
                    "channel_type": channel_info.get(channel_id, ("", ""))[1],
                    **_metrics(counters),
                }
                for channel_id, counters in by_channel.items()
            ),
            key=lambda r: r["discount_amount"],
            reverse=True,
        )

        warnings = []
        if totals["zero_price_nights"]:
            warnings.append(
                _(
                    "%(nights)s nights have a price of 0. They count in the "
                    "denominator, so they lower the share of discounted nights."
                )
                % {"nights": totals["zero_price_nights"]}
            )
        warnings.append(
            _(
                "The discount field does not tell whether a person or an "
                "automatic rule set it: channels can send discounts too."
            )
        )
        totals_metrics = _metrics(totals)
        return {
            "summary": _(
                "%(pct)s%% of %(nights)s nights had a discount, %(amount)s "
                "discounted; %(n50)s nights at 50%% or more (%(n100)s at 100%%)."
            )
            % {
                "pct": totals_metrics["discounted_nights_pct"],
                "nights": totals_metrics["nights"],
                "amount": totals_metrics["discount_amount"],
                "n50": totals_metrics["nights_50_or_more"],
                "n100": totals_metrics["nights_100"],
            },
            "period": dict(period, days=days, history=history),
            "definitions": [
                _(
                    "Nights: reservation lines dated in the period that occupy "
                    "availability (neither cancelled nor resold) of normal "
                    "reservations (staff and out of service are left out)."
                ),
                _(
                    "Discounted night: discount above 0%. The share is "
                    "measured over all the nights, including those priced at 0."
                ),
                _(
                    "Discount amount: night price minus final price, as stored "
                    "in the PMS; it includes taxes when the room prices do."
                ),
                _(
                    "Nights at 100% are reported apart as well "
                    "(discount_amount_100), since they are usually invitations."
                ),
                _(
                    "History: the same property over the %(periods)s previous "
                    "periods of the same length; change_pct_points compares "
                    "the share of discounted nights with their average."
                )
                % {"periods": HISTORY_PERIODS},
            ],
            "warnings": warnings,
            "result": {
                "totals": dict(
                    totals_metrics,
                    properties_with_discount=sum(
                        1 for c in by_property.values() if c["discounted_nights"]
                    ),
                ),
                "by_channel": channel_rows,
                "by_property": self._ai_limit_rows(property_rows, warnings, top),
                "rising": rising,
            },
        }
