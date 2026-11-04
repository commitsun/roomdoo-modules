from collections import defaultdict
from datetime import timedelta

from odoo import _, models

from odoo.addons.pms_ai_tool.tools import guarded_tool

from .pms_ai_operations_common import PROPERTY_IDS_INPUT

MAX_DAYS = 14


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    @guarded_tool(
        input_schema={
            "date_from": {
                "type": "string",
                "format": "date",
                "description": "First day to prepare for (YYYY-MM-DD). "
                "Default: tomorrow.",
            },
            "date_to": {
                "type": "string",
                "format": "date",
                "description": "Last day, included. At most "
                f"{MAX_DAYS} days in all. Default: date_from.",
            },
            "property_ids": PROPERTY_IDS_INPUT,
        },
        output_schema={
            "totals": {"type": "array", "items": {"type": "object"}},
            "by_property": {"type": "array", "items": {"type": "object"}},
        },
    )
    def _ai_pms_board_services_forecast(
        self, date_from=None, date_to=None, property_ids=None
    ):
        date_from = date_from or self._ai_ops_today() + timedelta(days=1)
        date_to = date_to or date_from
        period = self._ai_check_period(date_from, date_to, MAX_DAYS)
        warnings = []
        scope = self._ai_ops_scope(property_ids, warnings)
        domain = [
            ("date", ">=", date_from),
            ("date", "<=", date_to),
            ("pms_property_id", "in", scope),
            ("is_board_service", "=", True),
            ("reservation_id", "!=", False),
            ("reservation_id.reservation_type", "!=", "out"),
        ]
        lines = self.env["pms.service.line"]
        groups = lines.read_group(
            [*domain, ("reservation_id.state", "not in", ("draft", "cancel"))],
            ["day_qty:sum"],
            ["pms_property_id", "date:day", "product_id"],
            lazy=False,
        )
        # property -> day -> product -> quantity
        quantities = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
        totals = defaultdict(int)
        for group in groups:
            quantity = group["day_qty"] or 0
            product_id = group["product_id"][0]
            day = self._ai_ops_group_day(group)
            quantities[self._ai_ops_group_property(group)][day][product_id] += quantity
            totals[product_id] += quantity
        product_names = {
            product.id: product.name
            for product in self.env["product.product"].browse(list(totals))
        }

        def product_rows(by_product):
            return [
                {
                    "product_id": product_id,
                    "product": product_names.get(product_id, ""),
                    "quantity": quantity,
                }
                for product_id, quantity in sorted(
                    by_product.items(),
                    key=lambda item: (product_names.get(item[0], ""), item[0]),
                )
            ]

        names = self._ai_ops_property_names(list(quantities))
        rows = []
        for prop_id in sorted(quantities, key=lambda pid: names.get(pid, "")):
            rows.append(
                {
                    "property_id": prop_id,
                    "property": names.get(prop_id, ""),
                    "days": [
                        {"date": day, "products": product_rows(by_product)}
                        for day, by_product in sorted(quantities[prop_id].items())
                    ],
                }
            )
        without = set(scope) - set(quantities)
        if without:
            skipped = self._ai_ops_property_names(sorted(without))
            warnings.append(
                _("No board service in the period for: %s.")
                % ", ".join(sorted(skipped.values()))
            )
        draft_groups = lines.read_group(
            [*domain, ("reservation_id.state", "=", "draft")],
            ["day_qty:sum"],
            ["pms_property_id"],
            lazy=False,
        )
        drafts = sum(group["day_qty"] or 0 for group in draft_groups)
        if drafts:
            warnings.append(
                _(
                    "Units of board services of pre-reservations (not "
                    "confirmed) are not included: %s."
                )
                % drafts
            )
        total_rows = product_rows(totals)
        return {
            "summary": _("%(units)s units of %(products)s board service products.")
            % {
                "units": sum(totals.values()),
                "products": len(total_rows),
            },
            "period": period,
            "definitions": [
                _(
                    "Quantity: units of the board service product to prepare on "
                    "the day, that is the sum of the quantities of the service "
                    "lines dated that day. The date of a line is the day of "
                    "consumption, set by the product: a breakfast (consumed after "
                    "the night) is dated the day after each night, a dinner "
                    "(consumed before it) the night itself."
                ),
                _(
                    "Only board services of reservations that are neither "
                    "cancelled nor pre-reservations (not confirmed) are counted. "
                    "Staff stays count; out of service blocks do not."
                ),
            ],
            "warnings": warnings,
            "result": {
                "totals": total_rows,
                "by_property": self._ai_limit_rows(rows, warnings),
            },
        }
