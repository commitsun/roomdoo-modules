import re
from collections import defaultdict

from odoo import _, api, models
from odoo.exceptions import UserError

from ..tools import guarded_tool

PARAM_PRODUCTS = "pms_ai_tool.service_nights_product_ids"
MAX_DAYS = 93

SERVICE_NIGHTS_QUERY = """
SELECT sl.pms_property_id AS property_id,
       sl.product_id,
       coalesce(pt.consumed_on, 'before') AS consumed_on,
       r.state = 'cancel' AS cancelled,
       r.checkout - r.checkin AS stay_nights,
       CASE WHEN coalesce(pt.consumed_on, 'before') = 'after'
            THEN sl.date BETWEEN r.checkin + 1 AND r.checkout
            ELSE sl.date BETWEEN r.checkin AND r.checkout - 1
       END AS inside_stay,
       CASE WHEN coalesce(pt.consumed_on, 'before') = 'after'
            THEN sl.date = r.checkout
            ELSE sl.date = r.checkout - 1
       END AS last_night,
       count(DISTINCT (sl.reservation_id, sl.date)) AS room_nights,
       count(*) AS lines
  FROM pms_service_line sl
  JOIN pms_reservation r ON r.id = sl.reservation_id
  JOIN product_product pp ON pp.id = sl.product_id
  JOIN product_template pt ON pt.id = pp.product_tmpl_id
 WHERE sl.date BETWEEN %(date_from)s AND %(date_to)s
   AND sl.product_id = ANY(%(product_ids)s)
   AND sl.pms_property_id = ANY(%(property_ids)s)
   AND sl.day_qty > 0
GROUP BY 1, 2, 3, 4, 5, 6, 7
"""

BUCKETS = ("nights", "cancelled", "one_night_stays", "last_night", "outside_stay")


def _bucket(row):
    """Where a group of service lines goes, in order of precedence."""
    if row["cancelled"]:
        return "cancelled"
    if not row["inside_stay"]:
        return "outside_stay"
    if row["stay_nights"] == 1:
        return "one_night_stays"
    if row["last_night"]:
        return "last_night"
    return "nights"


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    @api.model
    def _ai_service_product_ids(self, product_ids=None):
        if not product_ids:
            param = (
                self.env["ir.config_parameter"].sudo().get_param(PARAM_PRODUCTS) or ""
            )
            product_ids = [int(pid) for pid in re.findall(r"\d+", param)]
        if not product_ids:
            raise UserError(
                _(
                    "No product given: pass product_ids or set the system "
                    "parameter %s."
                )
                % PARAM_PRODUCTS
            )
        products = self.env["product.product"].browse(product_ids).exists()
        missing = set(product_ids) - set(products.ids)
        if missing:
            raise UserError(
                _("Unknown products: %s.") % ", ".join(map(str, sorted(missing)))
            )
        return products

    @guarded_tool(
        input_schema={
            "date_from": {
                "type": "string",
                "format": "date",
                "description": "First date of the service lines (YYYY-MM-DD).",
            },
            "date_to": {
                "type": "string",
                "format": "date",
                "description": "Last date of the service lines, included. At "
                f"most {MAX_DAYS} days after date_from.",
            },
            "product_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "maxItems": 10,
                "description": "Service products (product.product ids). "
                "Default: the configured ones.",
            },
            "property_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "maxItems": 200,
                "description": "Properties to include. Default: all the user's.",
            },
        },
        output_schema={
            "totals": {"type": "object"},
            "by_property": {"type": "array", "items": {"type": "object"}},
            "products": {"type": "array", "items": {"type": "object"}},
        },
        required_inputs=["date_from", "date_to"],
    )
    def _ai_pms_service_nights(
        self, date_from, date_to, product_ids=None, property_ids=None
    ):
        period = self._ai_check_period(date_from, date_to, MAX_DAYS)
        products = self._ai_service_product_ids(product_ids)
        rows = self._ai_fetch(
            SERVICE_NIGHTS_QUERY,
            {
                "date_from": date_from,
                "date_to": date_to,
                "product_ids": products.ids,
                "property_ids": self._ai_scope_property_ids(property_ids),
            },
        )
        totals = dict.fromkeys(BUCKETS, 0)
        by_property = defaultdict(lambda: dict.fromkeys(BUCKETS, 0))
        repeated = 0
        for row in rows:
            bucket = _bucket(row)
            totals[bucket] += row["room_nights"]
            by_property[row["property_id"]][bucket] += row["room_nights"]
            repeated += row["lines"] - row["room_nights"]
        names = {
            prop.id: prop.name
            for prop in self.env["pms.property"].browse(list(by_property))
        }
        warnings = []
        if totals["outside_stay"]:
            warnings.append(
                _(
                    "%s service lines fall outside the stay of their reservation "
                    "(probably loaded by hand on another day): they are not "
                    "counted. Review them with the hotel."
                )
                % totals["outside_stay"]
            )
        if repeated:
            warnings.append(
                _(
                    "%s service lines repeat a night that already has the service "
                    "(loaded twice): each night is counted once."
                )
                % repeated
            )
        odd_products = products.filtered(
            lambda p: (p.consumed_on or "before") not in ("before", "after")
            or not p.per_day
        )
        if odd_products:
            warnings.append(
                _(
                    "The rules assume products charged per night before or after "
                    "it; check the result for: %s."
                )
                % ", ".join(odd_products.mapped("display_name"))
            )
        return {
            "summary": _(
                "%(nights)s nights with the service; left out: %(one)s in one-night "
                "stays, %(last)s on the last night, %(cancel)s in cancelled "
                "reservations; %(outside)s lines outside the stay."
            )
            % {
                "nights": totals["nights"],
                "one": totals["one_night_stays"],
                "last": totals["last_night"],
                "cancel": totals["cancelled"],
                "outside": totals["outside_stay"],
            },
            "period": period,
            "definitions": [
                _(
                    "Night: a room and night (reservation and date) with a service "
                    "line of the products dated in the period with a quantity above "
                    "0; counted once whatever the quantity or the number of lines."
                ),
                _(
                    "Left out: reservations of one night, the line of the last "
                    "night of the stay and cancelled reservations."
                ),
                _(
                    "Last night: for a product consumed after the night, the line "
                    "dated on the checkout date; consumed before the night, the line "
                    "of the day before the checkout."
                ),
                _(
                    "Outside the stay: a line whose date is not one of the nights "
                    "of its reservation for that product setting."
                ),
            ],
            "warnings": warnings,
            "result": {
                "totals": totals,
                "by_property": sorted(
                    (
                        {
                            "property_id": prop_id,
                            "property": names.get(prop_id, ""),
                            **counts,
                        }
                        for prop_id, counts in by_property.items()
                    ),
                    key=lambda r: -r["nights"],
                ),
                "products": [
                    {
                        "product_id": product.id,
                        "product": product.display_name,
                        "consumed_on": product.consumed_on or "before",
                        "per_day": product.per_day,
                    }
                    for product in products
                ],
            },
        }
