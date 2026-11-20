import re
from collections import defaultdict
from datetime import timedelta

from odoo import _, api, models
from odoo.exceptions import UserError

from ..tools import guarded_tool

PARAM_SALESPEOPLE = "pms_ai_tool.salesperson_ids"
PARAM_DEPENDENCY_PCT = "pms_ai_tool.customer_dependency_pct"
DEFAULT_DEPENDENCY_PCT = 40
MAX_DAYS = 93
# Spanish tax ids of natural persons (DNI and NIE): a company with one of these
# is most likely a self-employed person.
NATURAL_PERSON_VAT = re.compile(r"^(ES)?(\d{8}|[XYZ]\d{7})[A-Z]$")

SALESPEOPLE_INPUT = {
    "type": "array",
    "items": {"type": "integer"},
    "maxItems": 20,
    "description": "Users of the salespeople. Default: the configured list.",
}
DATE_FROM_INPUT = {"type": "string", "format": "date", "description": "YYYY-MM-DD"}
DATE_TO_INPUT = {
    "type": "string",
    "format": "date",
    "description": "YYYY-MM-DD, included.",
}

# Who brings a folio: the salesperson of its agency, else that of its customer.
FOLIO_SALESPERSON_SQL = """
    SELECT f.id AS folio_id,
           COALESCE(ag.user_id, cu.user_id) AS uid,
           CASE WHEN ag.user_id IS NOT NULL THEN 'agency' ELSE 'company' END
               AS channel
      FROM pms_folio f
      LEFT JOIN res_partner a ON a.id = f.agency_id
      LEFT JOIN res_partner ag ON ag.id = a.commercial_partner_id
                              AND ag.user_id = ANY(%(salespeople)s)
      LEFT JOIN res_partner c ON c.id = f.partner_id
      LEFT JOIN res_partner cu ON cu.id = c.commercial_partner_id
                              AND cu.user_id = ANY(%(salespeople)s)
     WHERE COALESCE(ag.user_id, cu.user_id) IS NOT NULL
       AND f.state <> 'cancel'
       AND f.pms_property_id = ANY(%(property_ids)s)
"""

SALES_QUERY = (
    """
WITH fol AS ("""
    + FOLIO_SALESPERSON_SQL
    + """
       AND f.last_checkout >= %(date_from)s
       AND f.first_checkin <= %(date_to)s
),
nights AS (
    SELECT fol.uid, fol.channel, r.folio_id, rl.price_day_total
      FROM pms_reservation_line rl
      JOIN pms_reservation r ON r.id = rl.reservation_id
      JOIN fol ON fol.folio_id = r.folio_id
     WHERE rl.date BETWEEN %(date_from)s AND %(date_to)s
       AND rl.occupies_availability
       AND r.reservation_type = 'normal'
),
zero_folios AS (
    -- Folios whose whole stay is worth 0: invoiced outside the PMS or courtesy.
    SELECT r.folio_id
      FROM pms_reservation_line rl
      JOIN pms_reservation r ON r.id = rl.reservation_id
     WHERE r.folio_id IN (SELECT folio_id FROM nights)
       AND rl.occupies_availability
       AND r.reservation_type = 'normal'
  GROUP BY r.folio_id
    HAVING coalesce(sum(rl.price_day_total), 0) = 0
)
SELECT n.uid, n.channel,
       count(*) AS nights,
       coalesce(sum(n.price_day_total), 0) AS production,
       count(DISTINCT n.folio_id) AS folios,
       count(*) FILTER (WHERE z.folio_id IS NULL) AS adr_nights,
       coalesce(sum(n.price_day_total) FILTER (WHERE z.folio_id IS NULL), 0)
           AS adr_production,
       count(DISTINCT z.folio_id) AS zero_folios,
       count(*) FILTER (WHERE z.folio_id IS NOT NULL) AS zero_nights
  FROM nights n
  LEFT JOIN zero_folios z ON z.folio_id = n.folio_id
GROUP BY n.uid, n.channel
"""
)

INVOICED_QUERY = """
SELECT cp.user_id AS uid, cp.id AS partner_id, cp.name, cp.vat,
       sum(m.amount_untaxed_signed) AS net,
       count(*) FILTER (WHERE m.move_type = 'out_invoice') AS invoices,
       count(*) FILTER (WHERE m.move_type = 'out_refund') AS refunds
  FROM account_move m
  JOIN res_partner cp ON cp.id = m.commercial_partner_id
 WHERE m.move_type IN ('out_invoice', 'out_refund')
   AND m.state = 'posted'
   AND m.invoice_date BETWEEN %(date_from)s AND %(date_to)s
   AND m.company_id = ANY(%(company_ids)s)
   AND (m.pms_property_id IS NULL OR m.pms_property_id = ANY(%(property_ids)s))
   AND cp.user_id = ANY(%(salespeople)s)
GROUP BY cp.user_id, cp.id, cp.name, cp.vat
"""

PORTFOLIO_QUERY = """
WITH assigned AS (
    SELECT id, user_id AS uid, vat, email
      FROM res_partner
     WHERE is_company AND active AND user_id = ANY(%(salespeople)s)
),
active AS (
    SELECT DISTINCT cp.id
      FROM pms_folio f
      JOIN res_partner x ON x.id IN (f.partner_id, f.agency_id)
      JOIN res_partner cp ON cp.id = x.commercial_partner_id
     WHERE cp.user_id = ANY(%(salespeople)s)
       AND f.state <> 'cancel'
       AND f.pms_property_id = ANY(%(property_ids)s)
       AND f.date_order >= %(active_start)s
       AND f.date_order < %(period_end)s
),
created AS (
    SELECT create_uid AS uid, count(*) AS new_companies
      FROM res_partner
     WHERE is_company
       AND create_uid = ANY(%(salespeople)s)
       AND create_date >= %(period_start)s
       AND create_date < %(period_end)s
  GROUP BY create_uid
),
overdue AS (
    SELECT m.id, cp.user_id AS uid, m.commercial_partner_id,
           m.invoice_date_due, m.amount_residual_signed AS residual
      FROM account_move m
      JOIN res_partner cp ON cp.id = m.commercial_partner_id
     WHERE m.move_type = 'out_invoice'
       AND m.state = 'posted'
       AND m.payment_state IN ('not_paid', 'partial')
       AND m.invoice_date_due <= %(date_to)s
       AND m.invoice_date >= %(overdue_from)s
       AND m.company_id = ANY(%(company_ids)s)
       AND (m.pms_property_id IS NULL
            OR m.pms_property_id = ANY(%(property_ids)s))
       AND cp.user_id = ANY(%(salespeople)s)
),
unapplied AS (
    -- Payments of the same customer registered on the folios of an overdue
    -- invoice and still open on the receivable account: the money arrived but
    -- was not applied. Each payment counts once, for the invoice due first.
    SELECT DISTINCT ON (pl.id) pl.id, o.id AS invoice_id,
           -pl.amount_residual AS amount
      FROM overdue o
      JOIN account_move_folio_ids_rel mf ON mf.account_move_id = o.id
      JOIN account_payment_folio_rel pf ON pf.folio_id = mf.folio_ids_id
      JOIN account_payment p ON p.id = pf.payment_id
      JOIN account_move_line pl ON pl.move_id = p.move_id
      JOIN account_account acc ON acc.id = pl.account_id
      JOIN res_partner pp ON pp.id = pl.partner_id
     WHERE acc.account_type = 'asset_receivable'
       AND pl.parent_state = 'posted'
       AND NOT pl.reconciled
       AND pl.amount_residual < 0
       AND pp.commercial_partner_id = o.commercial_partner_id
  ORDER BY pl.id, o.invoice_date_due, o.id
),
per_invoice AS (
    SELECT invoice_id, sum(amount) AS amount FROM unapplied GROUP BY invoice_id
)
SELECT u.uid,
       (SELECT count(*) FROM assigned a WHERE a.uid = u.uid) AS assigned,
       (SELECT count(*) FROM assigned a JOIN active ac ON ac.id = a.id
         WHERE a.uid = u.uid) AS active,
       (SELECT count(*) FROM assigned a
         WHERE a.uid = u.uid AND coalesce(a.vat, '') = '') AS without_vat,
       (SELECT count(*) FROM assigned a
         WHERE a.uid = u.uid AND coalesce(a.email, '') = '') AS without_email,
       coalesce((SELECT new_companies FROM created c WHERE c.uid = u.uid), 0)
           AS new_companies,
       (SELECT count(*) FROM overdue o WHERE o.uid = u.uid) AS overdue_invoices,
       coalesce((SELECT sum(residual) FROM overdue o WHERE o.uid = u.uid), 0)
           AS overdue_amount,
       (SELECT count(*) FROM overdue o JOIN per_invoice pi ON pi.invoice_id = o.id
         WHERE o.uid = u.uid) AS overdue_paid_on_folio_invoices,
       coalesce((SELECT sum(least(pi.amount, o.residual))
                   FROM overdue o JOIN per_invoice pi ON pi.invoice_id = o.id
                  WHERE o.uid = u.uid), 0) AS overdue_paid_on_folio_amount
  FROM unnest(%(salespeople)s::int[]) AS u(uid)
"""

COOLING_QUERY = """
WITH folios AS (
    SELECT DISTINCT cp.id AS partner_id, f.id AS folio_id,
           (f.date_order AT TIME ZONE 'UTC' AT TIME ZONE %(tz)s)::date AS day
      FROM pms_folio f
      JOIN res_partner x ON x.id IN (f.partner_id, f.agency_id)
      JOIN res_partner cp ON cp.id = x.commercial_partner_id
     WHERE cp.is_company
       AND cp.user_id = ANY(%(salespeople)s)
       AND f.state <> 'cancel'
       AND f.pms_property_id = ANY(%(property_ids)s)
       AND f.date_order >= %(before_start)s
       AND f.date_order < %(period_end)s
),
agg AS (
    SELECT partner_id,
           count(*) FILTER (WHERE day < %(recent_from)s) AS folios_before,
           count(*) FILTER (WHERE day >= %(recent_from)s) AS folios_recent,
           count(*) FILTER (WHERE day BETWEEN %(last_year_from)s
                                         AND %(last_year_to)s)
               AS folios_same_weeks_last_year,
           max(day) AS last_folio
      FROM folios
  GROUP BY partner_id
)
SELECT cp.user_id AS uid, cp.id AS partner_id, cp.name, cp.vat,
       a.folios_before, a.folios_same_weeks_last_year, a.last_folio
  FROM agg a
  JOIN res_partner cp ON cp.id = a.partner_id
 WHERE a.folios_recent = 0
   AND a.folios_before >= %(min_folios)s
"""


def _natural_person(vat):
    return bool(vat and NATURAL_PERSON_VAT.match(re.sub(r"[\s.-]", "", vat).upper()))


def _amount(value):
    return round(float(value or 0), 2)


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    # Helpers

    @api.model
    def _ai_salesperson_ids(self, salesperson_ids=None):
        if not salesperson_ids:
            param = (
                self.env["ir.config_parameter"].sudo().get_param(PARAM_SALESPEOPLE)
                or ""
            )
            salesperson_ids = [int(uid) for uid in re.findall(r"\d+", param)]
        if not salesperson_ids:
            raise UserError(
                _(
                    "No salespeople given: pass salesperson_ids or set the system "
                    "parameter %s."
                )
                % PARAM_SALESPEOPLE
            )
        users = self.env["res.users"].sudo().browse(salesperson_ids).exists()
        missing = set(salesperson_ids) - set(users.ids)
        if missing:
            raise UserError(
                _("Unknown salesperson users: %s.")
                % ", ".join(map(str, sorted(missing)))
            )
        return users

    @api.model
    def _ai_salesperson_definitions(self):
        return [
            _(
                "Salesperson: the user set as Salesperson on the company of the "
                "customer or of the agency."
            ),
        ]

    # Tool 2

    @guarded_tool(
        input_schema={
            "date_from": DATE_FROM_INPUT,
            "date_to": DATE_TO_INPUT,
            "salesperson_ids": SALESPEOPLE_INPUT,
            "property_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "maxItems": 200,
                "description": "Properties to include. Default: all the user's.",
            },
        },
        output_schema={"salespeople": {"type": "array", "items": {"type": "object"}}},
        required_inputs=["date_from", "date_to"],
    )
    def _ai_pms_salesperson_sales(
        self, date_from, date_to, salesperson_ids=None, property_ids=None
    ):
        period = self._ai_check_period(date_from, date_to, MAX_DAYS)
        users = self._ai_salesperson_ids(salesperson_ids)
        rows = self._ai_fetch(
            SALES_QUERY,
            {
                "salespeople": users.ids,
                "property_ids": self._ai_scope_property_ids(property_ids),
                "date_from": date_from,
                "date_to": date_to,
            },
        )
        by_user = defaultdict(list)
        zero_folios = zero_nights = 0
        for row in rows:
            zero_folios += row["zero_folios"]
            zero_nights += row["zero_nights"]
            by_user[row["uid"]].append(row)

        def figures(rows):
            adr_nights = sum(r["adr_nights"] for r in rows)
            adr_production = sum(float(r["adr_production"]) for r in rows)
            return {
                "nights": sum(r["nights"] for r in rows),
                "production": _amount(sum(float(r["production"]) for r in rows)),
                "folios": sum(r["folios"] for r in rows),
                "adr": _amount(adr_production / adr_nights) if adr_nights else None,
                "adr_nights": adr_nights,
            }

        salespeople = []
        for user in users:
            user_rows = by_user.get(user.id, [])
            salespeople.append(
                {
                    "salesperson_id": user.id,
                    "salesperson": user.name,
                    **figures(user_rows),
                    "by_channel": {row["channel"]: figures([row]) for row in user_rows},
                }
            )
        warnings = []
        if zero_folios:
            warnings.append(
                _(
                    "%(folios)s folios whose stay is worth 0 (%(nights)s nights) are "
                    "counted in nights and production but left out of the ADR: "
                    "they are usually invoiced outside the PMS or courtesies."
                )
                % {"folios": zero_folios, "nights": zero_nights}
            )
        return {
            "summary": _("Production of %(count)s salespeople from %(from)s to %(to)s.")
            % {"count": len(users), "from": date_from, "to": date_to},
            "period": period,
            "definitions": self._ai_salesperson_definitions()
            + [
                _(
                    "Production attribution: the salesperson of the folio's agency "
                    "and, if it has none, that of the customer company "
                    "(by_channel 'agency' / 'company')."
                ),
                _(
                    "Nights: room nights dated in the period that occupy "
                    "availability in normal reservations of folios not cancelled."
                ),
                _(
                    "Production: final night price stored in the PMS (after "
                    "discounts); it includes taxes when the room prices do."
                ),
                _(
                    "ADR: production / nights, leaving out folios whose whole stay "
                    "is worth 0. Consolidated group folios do count."
                ),
            ],
            "warnings": warnings,
            "result": {"salespeople": salespeople},
        }

    # Tool 3

    @guarded_tool(
        input_schema={
            "date_from": DATE_FROM_INPUT,
            "date_to": DATE_TO_INPUT,
            "salesperson_ids": SALESPEOPLE_INPUT,
            "top": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "description": "Customers listed per salesperson (5).",
            },
        },
        output_schema={"salespeople": {"type": "array", "items": {"type": "object"}}},
        required_inputs=["date_from", "date_to"],
    )
    def _ai_pms_salesperson_top_customers(
        self, date_from, date_to, salesperson_ids=None, top=5
    ):
        period = self._ai_check_period(date_from, date_to, MAX_DAYS)
        users = self._ai_salesperson_ids(salesperson_ids)
        threshold = float(
            self.env["ir.config_parameter"]
            .sudo()
            .get_param(PARAM_DEPENDENCY_PCT, DEFAULT_DEPENDENCY_PCT)
        )
        rows = self._ai_fetch(
            INVOICED_QUERY,
            {
                "salespeople": users.ids,
                "company_ids": self._ai_scope_company_ids(),
                "property_ids": self._ai_scope_property_ids(),
                "date_from": date_from,
                "date_to": date_to,
            },
        )
        by_user = defaultdict(list)
        for row in rows:
            by_user[row["uid"]].append(row)
        warnings = []
        salespeople = []
        for user in users:
            customers = sorted(by_user.get(user.id, []), key=lambda r: -r["net"])
            total = sum(float(r["net"]) for r in customers)
            negative = [r for r in customers if r["net"] < 0]
            if negative:
                warnings.append(
                    _(
                        "%(name)s: %(count)s customers have a negative total (more "
                        "refunds than invoices in the period)."
                    )
                    % {"name": user.name, "count": len(negative)}
                )
            listed = []
            for position, row in enumerate(customers[:top], start=1):
                share = round(100 * float(row["net"]) / total, 1) if total else None
                listed.append(
                    {
                        "position": position,
                        "partner_id": row["partner_id"],
                        "customer": row["name"],
                        "self_employed": _natural_person(row["vat"]),
                        "invoiced_untaxed": _amount(row["net"]),
                        "invoices": row["invoices"],
                        "refunds": row["refunds"],
                        "share_pct": share,
                        "dependency": bool(share and share > threshold),
                    }
                )
            salespeople.append(
                {
                    "salesperson_id": user.id,
                    "salesperson": user.name,
                    "invoiced_untaxed": _amount(total),
                    "customers": len(customers),
                    "top": listed,
                }
            )
        return {
            "summary": _(
                "Top customers of %(count)s salespeople from %(from)s to %(to)s."
            )
            % {"count": len(users), "from": date_from, "to": date_to},
            "period": period,
            "definitions": self._ai_salesperson_definitions()
            + [
                _(
                    "Invoicing attribution: the salesperson of the invoiced company "
                    "(commercial partner of the invoice)."
                ),
                _(
                    "Invoiced: untaxed amount of the posted customer invoices dated "
                    "in the period minus their refunds."
                ),
                _(
                    "dependency: the customer is more than %(pct)s%% of what the "
                    "salesperson invoiced. self_employed: the tax id is a natural "
                    "person's (DNI/NIE)."
                )
                % {"pct": threshold},
            ],
            "warnings": warnings,
            "result": {"salespeople": salespeople},
        }

    # Tool 4

    @guarded_tool(
        input_schema={
            "date_to": DATE_TO_INPUT,
            "date_from": {
                "type": "string",
                "format": "date",
                "description": "Start of the period for new companies "
                "(default: 6 days before date_to).",
            },
            "salesperson_ids": SALESPEOPLE_INPUT,
            "active_weeks": {
                "type": "integer",
                "minimum": 1,
                "maximum": 52,
                "description": "Weeks back that make a company active (8).",
            },
        },
        output_schema={"salespeople": {"type": "array", "items": {"type": "object"}}},
        required_inputs=["date_to"],
    )
    def _ai_pms_salesperson_portfolio(
        self, date_to, date_from=None, salesperson_ids=None, active_weeks=8
    ):
        date_from = date_from or date_to - timedelta(days=6)
        period = self._ai_check_period(date_from, date_to, MAX_DAYS)
        users = self._ai_salesperson_ids(salesperson_ids)
        active_from = date_to - timedelta(weeks=active_weeks) + timedelta(days=1)
        period_start, period_end = self._ai_utc_bounds(date_from, date_to)
        active_start = self._ai_utc_bounds(active_from, date_to)[0]
        rows = self._ai_fetch(
            PORTFOLIO_QUERY,
            {
                "salespeople": users.ids,
                "property_ids": self._ai_scope_property_ids(),
                "company_ids": self._ai_scope_company_ids(),
                "date_to": date_to,
                "period_start": period_start,
                "period_end": period_end,
                "active_start": active_start,
                # 365 days including date_to, as in the original report.
                "overdue_from": date_to - timedelta(days=364),
            },
        )
        by_user = {row["uid"]: row for row in rows}
        salespeople = []
        for user in users:
            row = by_user[user.id]
            salespeople.append(
                {
                    "salesperson_id": user.id,
                    "salesperson": user.name,
                    "assigned_companies": row["assigned"],
                    "active_companies": row["active"],
                    "active_pct": round(100 * row["active"] / row["assigned"], 1)
                    if row["assigned"]
                    else None,
                    "new_companies": row["new_companies"],
                    "without_vat": row["without_vat"],
                    "without_email": row["without_email"],
                    "overdue_invoices": row["overdue_invoices"],
                    "overdue_amount": _amount(row["overdue_amount"]),
                    "overdue_paid_on_folio_invoices": row[
                        "overdue_paid_on_folio_invoices"
                    ],
                    "overdue_paid_on_folio_amount": _amount(
                        row["overdue_paid_on_folio_amount"]
                    ),
                }
            )
        return {
            "summary": _("Portfolio of %(count)s salespeople on %(date)s.")
            % {"count": len(users), "date": date_to},
            "period": dict(period, active_from=active_from),
            "definitions": self._ai_salesperson_definitions()
            + [
                _(
                    "Assigned companies: active companies with the salesperson set. "
                    "Active: they have a folio not cancelled dated in the last "
                    "%(weeks)s weeks, as customer or agency."
                )
                % {"weeks": active_weeks},
                _(
                    "New companies: companies the salesperson created in Odoo between "
                    "date_from and date_to (it is not the same as assigned)."
                ),
                _(
                    "Overdue: posted customer invoices of the last 365 days due on "
                    "or before date_to and not fully paid, residual amount."
                ),
                _(
                    "Overdue paid on the folio: overdue invoices whose folios hold a "
                    "payment of the same customer still open on the receivable "
                    "account, up to the invoice residual; each payment counts once, "
                    "for the invoice due first. The money arrived but was not "
                    "applied."
                ),
                _("Dates of folios and companies are taken in the time zone %s.")
                % self._ai_timezone(),
            ],
            "warnings": [],
            "result": {"salespeople": salespeople},
        }

    # Tool 5

    @guarded_tool(
        input_schema={
            "date_to": DATE_TO_INPUT,
            "salesperson_ids": SALESPEOPLE_INPUT,
            "recent_weeks": {
                "type": "integer",
                "minimum": 1,
                "maximum": 26,
                "description": "Weeks without folios that make a company cool (8).",
            },
            "min_folios": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "Folios in the previous 12 months to count (3).",
            },
            "top": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "description": "Companies listed per salesperson (10).",
            },
        },
        output_schema={"salespeople": {"type": "array", "items": {"type": "object"}}},
        required_inputs=["date_to"],
    )
    def _ai_pms_salesperson_cooling_customers(
        self, date_to, salesperson_ids=None, recent_weeks=8, min_folios=3, top=10
    ):
        users = self._ai_salesperson_ids(salesperson_ids)
        recent_from = date_to - timedelta(weeks=recent_weeks) + timedelta(days=1)
        before_from = recent_from - timedelta(days=365)
        last_year_from = recent_from - timedelta(days=365)
        last_year_to = date_to - timedelta(days=365)
        before_start, period_end = self._ai_utc_bounds(before_from, date_to)
        rows = self._ai_fetch(
            COOLING_QUERY,
            {
                "salespeople": users.ids,
                "property_ids": self._ai_scope_property_ids(),
                "tz": self._ai_timezone(),
                "before_start": before_start,
                "period_end": period_end,
                "recent_from": recent_from,
                "last_year_from": last_year_from,
                "last_year_to": last_year_to,
                "min_folios": min_folios,
            },
        )
        by_user = defaultdict(list)
        for row in rows:
            by_user[row["uid"]].append(row)
        salespeople = []
        seasonal_total = 0
        for user in users:
            cooling = sorted(
                by_user.get(user.id, []),
                key=lambda r: (-r["folios_before"], r["name"] or ""),
            )
            seasonal = sum(1 for r in cooling if not r["folios_same_weeks_last_year"])
            seasonal_total += seasonal
            salespeople.append(
                {
                    "salesperson_id": user.id,
                    "salesperson": user.name,
                    "cooling_companies": len(cooling),
                    "seasonal_candidates": seasonal,
                    "top": [
                        {
                            "partner_id": row["partner_id"],
                            "company": row["name"],
                            "self_employed": _natural_person(row["vat"]),
                            "without_vat": not row["vat"],
                            "folios_previous_12_months": row["folios_before"],
                            "last_folio": row["last_folio"],
                            "weeks_without_folios": (date_to - row["last_folio"]).days
                            // 7,
                            "folios_same_weeks_last_year": row[
                                "folios_same_weeks_last_year"
                            ],
                            "maybe_seasonal": not row["folios_same_weeks_last_year"],
                        }
                        for row in cooling[:top]
                    ],
                }
            )
        warnings = []
        if seasonal_total:
            warnings.append(
                _(
                    "%s cooling companies had no folios in the same weeks last year "
                    "either: it may be seasonality rather than a lost customer."
                )
                % seasonal_total
            )
        return {
            "summary": _("Cooling companies of %(count)s salespeople on %(date)s.")
            % {"count": len(users), "date": date_to},
            "period": {
                "date_to": date_to,
                "recent_from": recent_from,
                "previous_12_months_from": before_from,
                "same_weeks_last_year": [last_year_from, last_year_to],
            },
            "definitions": self._ai_salesperson_definitions()
            + [
                _(
                    "Cooling company: an assigned company with at least %(min)s "
                    "folios not cancelled in the 12 months before the last "
                    "%(weeks)s weeks and none in those weeks, as customer or "
                    "agency, by folio date."
                )
                % {"min": min_folios, "weeks": recent_weeks},
                _(
                    "maybe_seasonal: it had no folios in the same weeks last year "
                    "either. self_employed: the tax id is a natural person's."
                ),
            ],
            "warnings": warnings,
            "result": {"salespeople": salespeople},
        }
