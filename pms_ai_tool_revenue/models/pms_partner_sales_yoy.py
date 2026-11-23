from collections import defaultdict
from datetime import date, timedelta

from odoo import _, models

from odoo.addons.pms_ai_tool.tools import guarded_tool

from . import revenue_common as rc

MAX_YEARS = 4

KINDS = ("accommodation", "fnb", "other_services")

# Services are food and beverage when they belong to a board service or when
# their product is one of the products of the board services of the PMS.
# Amounts are the final prices with VAT, assigned by the date of the stay (or
# of the service line). Only normal reservations count (staff and out of
# service are left out), and cancelled ones carry no sale.
SALES_CTE = """
sales AS (
    SELECT rl.pms_property_id AS property_id,
           extract(year FROM rl.date)::int AS yr,
           (rl.date > %(today)s) AS future,
           'accommodation' AS kind,
           r.agency_id,
           f.partner_id AS folio_partner_id,
           rl.price_day_total AS amount
      FROM pms_reservation_line rl
      JOIN pms_reservation r ON r.id = rl.reservation_id
      JOIN pms_folio f ON f.id = r.folio_id
     WHERE rl.date BETWEEN %(date_from)s AND %(date_to)s
       AND rl.pms_property_id = ANY(%(property_ids)s)
       AND rl.occupies_availability
       AND r.reservation_type = 'normal'
       AND coalesce(rl.overnight_room, true)
     UNION ALL
    SELECT sl.pms_property_id,
           extract(year FROM sl.date)::int,
           (sl.date > %(today)s),
           CASE WHEN sl.is_board_service
                     OR sl.product_id IN (SELECT product_id FROM pms_board_service_line)
                THEN 'fnb' ELSE 'other_services' END,
           r.agency_id,
           f.partner_id,
           sl.price_day_total
      FROM pms_service_line sl
      JOIN pms_reservation r ON r.id = sl.reservation_id
      JOIN pms_folio f ON f.id = r.folio_id
     WHERE sl.date BETWEEN %(date_from)s AND %(date_to)s
       AND sl.pms_property_id = ANY(%(property_ids)s)
       AND r.reservation_type = 'normal'
       AND r.state <> 'cancel'
)"""

# The agency (its company, as in the other revenue tools) or the customer
# company of the folio: the commercial partner when it is a company that is not
# an agency.
PARTNER_JOINS = {
    "agency": """
  JOIN res_partner a ON a.id = s.agency_id
  JOIN res_partner cp ON cp.id = coalesce(a.commercial_partner_id, a.id)""",
    "company": """
  JOIN res_partner a ON a.id = s.folio_partner_id
  JOIN res_partner cp ON cp.id = coalesce(a.commercial_partner_id, a.id)
                     AND cp.is_company AND NOT coalesce(cp.is_agency, false)""",
}

SALES_QUERY = (
    "WITH "
    + SALES_CTE
    + "\nSELECT s.property_id, cp.id AS key_id, s.yr, s.kind,"
    + "\n       sum(s.amount) AS amount,"
    + "\n       sum(s.amount) FILTER (WHERE s.future) AS future_amount"
    + "\n  FROM sales s{joins}"
    + "\n GROUP BY s.property_id, cp.id, s.yr, s.kind"
)


def _new_year():
    return dict.fromkeys(KINDS, 0.0)


def _year_figures(cell):
    figures = {kind: rc.money(cell[kind]) for kind in KINDS}
    figures["total"] = rc.money(sum(cell.values()))
    return figures


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    def _ai_rev_partner_sales(self, split, params):
        """``{partner: {property: {year: kinds}}}`` and the future sales."""
        sales = defaultdict(lambda: defaultdict(lambda: defaultdict(_new_year)))
        future = defaultdict(lambda: defaultdict(float))
        query = SALES_QUERY.replace("{joins}", PARTNER_JOINS[split])
        for row in self._ai_fetch(query, params):
            year, amount = row["yr"], float(row["amount"] or 0)
            if year in params["years"]:
                sales[row["key_id"]][row["property_id"]][year][row["kind"]] += amount
            if year == params["current_year"]:
                future[row["key_id"]][row["property_id"]] += float(
                    row["future_amount"] or 0
                )
        return sales, future

    @guarded_tool(
        input_schema={
            "years": {
                "type": "array",
                "items": {"type": "integer", "minimum": 2000, "maximum": 2100},
                "maxItems": MAX_YEARS,
                "description": "Years to compare (YYYY), up to "
                f"{MAX_YEARS}. Default: this year and the previous one.",
            },
            "min_sales_amount": {
                "type": "number",
                "minimum": 0,
                "description": "Only agencies and companies with at least this "
                "much sales (with VAT) in some of the years. Default 0: all.",
            },
            "property_ids": rc.PROPERTY_IDS_INPUT,
        },
        output_schema={
            "years": {"type": "array", "items": {"type": "integer"}},
            "by_agency": {"type": "array", "items": {"type": "object"}},
            "by_company": {"type": "array", "items": {"type": "object"}},
        },
        log_result=False,
    )
    def _ai_pms_partner_sales_yoy(
        self, years=None, min_sales_amount=0, property_ids=None
    ):
        local_now, _utc_now = self._ai_rev_now()
        today = local_now.date()
        years = sorted(set(years or [today.year, today.year - 1]))
        property_ids = self._ai_scope_property_ids(property_ids)
        all_years = set(years) | {today.year}
        params = {
            "date_from": date(min(all_years), 1, 1),
            "date_to": date(max(all_years), 12, 31),
            "today": today,
            "property_ids": property_ids,
            "years": years,
            "current_year": today.year,
        }
        names = self._ai_rev_property_names(property_ids)
        warnings = []
        result = {"years": years}
        totals = {}
        for split in ("agency", "company"):
            sales, future = self._ai_rev_partner_sales(split, params)
            partners = {
                p.id: p.name
                for p in self.env["res.partner"]
                .sudo()
                .with_context(active_test=False)
                .browse(list(sales) + list(future))
            }
            rows = []
            grand = defaultdict(_new_year)
            grand_future = 0.0
            for key in set(sales) | set(future):
                year_cells = defaultdict(_new_year)
                by_property = []
                for prop_id in sorted(set(sales[key]) | set(future[key])):
                    for year, cell in sales[key][prop_id].items():
                        for kind in KINDS:
                            year_cells[year][kind] += cell[kind]
                    by_property.append(
                        {
                            "property_id": prop_id,
                            "property": names.get(prop_id, ""),
                            "years": {
                                str(year): _year_figures(sales[key][prop_id][year])
                                for year in years
                            },
                            "future_current_year": rc.money(future[key][prop_id]),
                        }
                    )
                year_totals = {year: sum(year_cells[year].values()) for year in years}
                if max(year_totals.values()) < min_sales_amount:
                    continue
                if not any(year_totals.values()) and not any(future[key].values()):
                    continue
                for year in years:
                    for kind in KINDS:
                        grand[year][kind] += year_cells[year][kind]
                key_future = sum(future[key].values())
                grand_future += key_future
                rows.append(
                    {
                        "id": key,
                        "name": partners.get(key, ""),
                        "years": {
                            str(year): _year_figures(year_cells[year]) for year in years
                        },
                        "future_current_year": rc.money(key_future),
                        "by_property": by_property,
                    }
                )
            rows.sort(
                key=lambda row: (
                    -row["years"][str(years[-1])]["total"],
                    row["name"],
                )
            )
            totals[split] = {
                "partners": len(rows),
                "years": {str(year): _year_figures(grand[year]) for year in years},
                "future_current_year": rc.money(grand_future),
            }
            result["by_" + split] = self._ai_limit_rows(rows, warnings)
        result["totals"] = totals
        return {
            "summary": _(
                "Sales with VAT of %(agencies)s agencies and %(companies)s "
                "companies in the years %(years)s."
            )
            % {
                "agencies": totals["agency"]["partners"],
                "companies": totals["company"]["partners"],
                "years": ", ".join(map(str, years)),
            },
            "period": {
                "years": years,
                "future_from": today + timedelta(days=1),
                "future_to": date(today.year, 12, 31),
            },
            "definitions": [
                _(
                    "Sales: accommodation + food and beverage + other services, "
                    "with VAT (final prices as the PMS stores them), assigned to "
                    "the year of the stay date of each night or service line. "
                    "Normal reservations only; cancelled reservations carry no "
                    "sale; pre-reservations count."
                ),
                _(
                    "Food and beverage: service lines of board services, or of "
                    "products that are used in some board service of the PMS. "
                    "The rest of the services are other services."
                ),
                _(
                    "Agency: the agency company of the folio. Company: the "
                    "customer of the folio when it is a company that is not an "
                    "agency. A folio can count for both."
                ),
                _(
                    "future_current_year: sales of the current year with stay "
                    "date from tomorrow to 31 December, already on the books "
                    "(included in the total of the current year)."
                ),
            ],
            "warnings": warnings,
            "result": result,
        }
