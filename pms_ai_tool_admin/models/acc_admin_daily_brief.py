from datetime import timedelta

from odoo import _, fields, models
from odoo.exceptions import AccessError

from odoo.addons.pms_ai_tool.tools import guarded_tool

from .common import PROPERTY_IDS_INPUT, amount

TOP_ITEMS = 10
DUE_SOON_DAYS = 7
DUPLICATE_LOOKBACK_DAYS = 365
VENDOR_BILL_TYPES = ["in_invoice", "in_refund"]

# Scope of the SQL queries on account_move (alias m); same rule as
# _ai_bill_scope_domain: moves without property only when no property filter.
SQL_MOVE_SCOPE = """
    m.company_id = ANY(%(company_ids)s)
    AND (
        m.pms_property_id = ANY(%(property_ids)s)
        OR (%(with_unassigned)s AND m.pms_property_id IS NULL)
    )
"""

SQL_BILLS_TO_REVIEW = (
    """
    SELECT m.id,
           m.partner_id IS NULL AS no_partner,
           m.invoice_date IS NULL AS no_invoice_date,
           COALESCE(bool_or(l.id IS NOT NULL AND l.account_id IS NULL), false)
               AS no_account,
           COALESCE(bool_or(l.id IS NOT NULL AND NOT EXISTS (
               SELECT 1 FROM account_move_line_account_tax_rel rel
               WHERE rel.account_move_line_id = l.id
           )), false) AS no_tax,
           count(l.id) = 0 AS no_lines
    FROM account_move m
    LEFT JOIN account_move_line l
        ON l.move_id = m.id AND l.display_type = 'product'
    WHERE m.state = 'draft'
      AND m.move_type = ANY(%(types)s)
      AND """
    + SQL_MOVE_SCOPE
    + """
    GROUP BY m.id
    """
)

SQL_DUPLICATES = (
    """
    WITH bills AS (
        SELECT m.id, m.commercial_partner_id AS partner_id, m.move_type,
               lower(btrim(m.ref)) AS ref_key, m.invoice_date, m.amount_total,
               m.currency_id
        FROM account_move m
        WHERE m.move_type = ANY(%(types)s)
          AND m.state IN ('draft', 'posted')
          AND m.commercial_partner_id IS NOT NULL
          AND (m.state = 'draft' OR m.date >= %(since)s)
          AND """
    + SQL_MOVE_SCOPE
    + """
    )
    SELECT 'reference' AS rule, array_agg(id ORDER BY id) AS ids
    FROM bills
    WHERE COALESCE(ref_key, '') <> ''
    GROUP BY partner_id, move_type, ref_key
    HAVING count(*) > 1
    UNION ALL
    SELECT 'total_and_date' AS rule, array_agg(id ORDER BY id) AS ids
    FROM bills
    WHERE invoice_date IS NOT NULL AND amount_total <> 0
    GROUP BY partner_id, move_type, invoice_date, amount_total, currency_id
    HAVING count(*) > 1
    """
)

SQL_OPEN_PAYMENTS = (
    """
    WITH open_payments AS (
        SELECT p.id, p.partner_type, p.payment_type, p.partner_id,
               m.name, m.date, m.company_id, m.pms_property_id,
               abs(sum(l.amount_residual)) AS residual
        FROM account_payment p
        JOIN account_move m ON m.id = p.move_id
        JOIN account_move_line l ON l.move_id = m.id
        JOIN account_account a ON a.id = l.account_id
        WHERE m.state = 'posted'
          AND NOT COALESCE(p.is_internal_transfer, false)
          AND a.account_type IN ('asset_receivable', 'liability_payable')
          AND NOT l.reconciled
          AND l.amount_residual <> 0
          AND """
    + SQL_MOVE_SCOPE
    + """
        GROUP BY p.id, m.id
    )
    """
)

SQL_OPEN_PAYMENTS_TOTALS = (
    SQL_OPEN_PAYMENTS
    + """
    SELECT partner_type, count(*) AS payments, sum(residual) AS residual,
           min(date) AS oldest
    FROM open_payments
    GROUP BY partner_type
    """
)

SQL_OPEN_PAYMENTS_TOP = (
    SQL_OPEN_PAYMENTS
    + """
    SELECT * FROM open_payments ORDER BY date, id LIMIT %(limit)s
    """
)


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    def _ai_brief_scope(self, company_id, property_ids):
        """Companies and properties of the brief, checked against the user's."""
        company_ids = self._ai_scope_company_ids()
        if company_id:
            if company_id not in company_ids:
                raise AccessError(
                    _("You have no access to the company %s.") % company_id
                )
            company_ids = [company_id]
        return {
            "company_ids": company_ids,
            "property_ids": self._ai_scope_property_ids(property_ids),
            "with_unassigned": not property_ids,
        }

    def _ai_brief_domain(self, scope):
        domain = [("company_id", "in", scope["company_ids"])]
        if scope["with_unassigned"]:
            return domain + [
                "|",
                ("pms_property_id", "=", False),
                ("pms_property_id", "in", scope["property_ids"]),
            ]
        return domain + [("pms_property_id", "in", scope["property_ids"])]

    def _ai_brief_totals(self, domain, field):
        """Number of moves and absolute sum of ``field`` for a domain."""
        groups = self.env["account.move"].read_group(
            domain, [f"{field}:sum"], [], lazy=False
        )
        group = groups[0] if groups else {}
        return group.get("__count") or 0, amount(abs(group.get(field) or 0))

    def _ai_brief_bill_row(self, move, today):
        return {
            "id": move.id,
            "number": move.name if move.name and move.name != "/" else None,
            "type": move.move_type,
            "state": move.state,
            "supplier": move.commercial_partner_id.name or None,
            "supplier_reference": move.ref or None,
            "company": move.company_id.name,
            "property": move.pms_property_id.name or None,
            "invoice_date": move.invoice_date or None,
            "due_date": move.invoice_date_due or None,
            "total": amount(abs(move.amount_total_signed)),
            "pending": amount(abs(move.amount_residual_signed))
            if move.state == "posted"
            else None,
            "age_days": (today - move.create_date.date()).days,
        }

    def _ai_brief_customer_name(self, partner):
        """Companies and agencies by name; individuals (guests) are hidden."""
        partner = partner.commercial_partner_id
        if partner.is_company or partner.is_agency:
            return partner.name
        return None

    def _ai_brief_draft_bills(self, scope, today):
        domain = self._ai_brief_domain(scope) + [
            ("move_type", "in", VENDOR_BILL_TYPES),
            ("state", "=", "draft"),
        ]
        Move = self.env["account.move"]
        count, total = self._ai_brief_totals(domain, "amount_total_signed")
        top = Move.search(domain, order="create_date, id", limit=TOP_ITEMS)
        items = [self._ai_brief_bill_row(move, today) for move in top]
        return {
            "count": count,
            "total": total,
            "oldest_age_days": items[0]["age_days"] if items else None,
            "items": items,
            "next_tools": ["acc_vendor_bill_edit", "acc_vendor_bills_post"],
        }

    def _ai_brief_bills_to_review(self, scope, today):
        Move = self.env["account.move"]
        reasons = {}
        flagged = Move.search(
            self._ai_brief_domain(scope)
            + [
                ("move_type", "in", VENDOR_BILL_TYPES),
                ("state", "in", ("draft", "posted")),
                ("to_check", "=", True),
            ]
        )
        for move in flagged:
            reasons[move.id] = ["to_check"]
        incomplete = 0
        rows = self._ai_fetch(SQL_BILLS_TO_REVIEW, dict(scope, types=VENDOR_BILL_TYPES))
        for row in rows:
            missing = [
                key
                for key in (
                    "no_partner",
                    "no_invoice_date",
                    "no_account",
                    "no_tax",
                    "no_lines",
                )
                if row[key]
            ]
            if missing:
                incomplete += 1
                reasons.setdefault(row["id"], []).extend(missing)
        moves = Move.search(
            [("id", "in", list(reasons))], order="create_date, id", limit=TOP_ITEMS
        )
        items = []
        for move in moves:
            item = self._ai_brief_bill_row(move, today)
            item["reasons"] = reasons[move.id]
            items.append(item)
        return {
            "count": len(reasons),
            "to_check": len(flagged),
            "incomplete_drafts": incomplete,
            "items": items,
            "next_tools": ["acc_vendor_bill_edit"],
        }

    def _ai_brief_duplicates(self, scope, today):
        rows = self._ai_fetch(
            SQL_DUPLICATES,
            dict(
                scope,
                types=VENDOR_BILL_TYPES,
                since=today - timedelta(days=DUPLICATE_LOOKBACK_DAYS),
            ),
        )
        groups = {}
        for row in rows:
            key = tuple(row["ids"])
            groups.setdefault(key, []).append(row["rule"])
        Move = self.env["account.move"]
        candidates = []
        for ids, rules in groups.items():
            moves = Move.browse(ids)
            candidates.append(
                (
                    # Groups with a draft first: the draft is usually the copy.
                    not any(move.state == "draft" for move in moves),
                    -max(move.create_date.timestamp() for move in moves),
                    ids,
                    rules,
                    moves,
                )
            )
        candidates.sort(key=lambda c: c[:3])
        items = [
            {
                "rules": sorted(rules),
                "supplier": moves[0].commercial_partner_id.name,
                "bills": [self._ai_brief_bill_row(move, today) for move in moves],
            }
            for _has_draft, _newest, _ids, rules, moves in candidates[:TOP_ITEMS]
        ]
        return {
            "count": len(groups),
            "bills": len({move_id for ids in groups for move_id in ids}),
            "items": items,
            "next_tools": ["acc_vendor_bill_edit"],
        }

    def _ai_brief_payables(self, scope, today):
        Move = self.env["account.move"]
        payable = self._ai_brief_domain(scope) + [
            ("move_type", "=", "in_invoice"),
            ("state", "=", "posted"),
            ("amount_residual", ">", 0),
        ]
        sections = {}
        for key, extra in (
            ("overdue", [("invoice_date_due", "<", today)]),
            (
                "due_soon",
                [
                    ("invoice_date_due", ">=", today),
                    ("invoice_date_due", "<=", today + timedelta(days=DUE_SOON_DAYS)),
                ],
            ),
        ):
            domain = payable + extra
            count, pending = self._ai_brief_totals(domain, "amount_residual_signed")
            top = Move.search(domain, order="invoice_date_due, id", limit=TOP_ITEMS)
            sections[key] = {
                "count": count,
                "pending": pending,
                "items": [self._ai_brief_bill_row(move, today) for move in top],
            }
        return dict(
            sections,
            next_tools=["acc_payment_register", "acc_payment_order_prepare"],
        )

    def _ai_brief_customer_overdue(self, scope, today):
        domain = self._ai_brief_domain(scope) + [
            ("move_type", "=", "out_invoice"),
            ("state", "=", "posted"),
            ("amount_residual_signed", ">", 0),
            ("invoice_date_due", "<", today),
        ]
        count, pending = self._ai_brief_totals(domain, "amount_residual_signed")
        top = self.env["account.move"].search(
            domain, order="amount_residual_signed desc, id", limit=TOP_ITEMS
        )
        return {
            "count": count,
            "pending": pending,
            "items": [
                {
                    "id": move.id,
                    "number": move.name,
                    "customer": self._ai_brief_customer_name(move.partner_id),
                    "property": move.pms_property_id.name or None,
                    "due_date": move.invoice_date_due,
                    "days_overdue": (today - move.invoice_date_due).days,
                    "pending": amount(move.amount_residual_signed),
                }
                for move in top
            ],
            "next_tools": ["pms_customer_overdue"],
        }

    def _ai_brief_open_payments(self, scope, today):
        totals = self._ai_fetch(SQL_OPEN_PAYMENTS_TOTALS, scope)
        top = self._ai_fetch(SQL_OPEN_PAYMENTS_TOP, dict(scope, limit=TOP_ITEMS))
        Partner = self.env["res.partner"]
        items = []
        for row in top:
            partner = Partner.browse(row["partner_id"])
            if row["partner_type"] == "supplier":
                name = partner.commercial_partner_id.name or None
            else:
                name = self._ai_brief_customer_name(partner) if partner else None
            items.append(
                {
                    "payment_id": row["id"],
                    "entry": row["name"],
                    "kind": row["partner_type"],
                    "direction": row["payment_type"],
                    "partner": name,
                    "date": row["date"],
                    "age_days": (today - row["date"]).days,
                    "open_amount": amount(row["residual"]),
                }
            )
        by_kind = {
            row["partner_type"]: {
                "count": row["payments"],
                "open_amount": amount(row["residual"]),
                "oldest_date": row["oldest"],
            }
            for row in totals
        }
        return {
            "count": sum(row["payments"] for row in totals),
            "open_amount": amount(sum(row["residual"] or 0 for row in totals)),
            "by_kind": by_kind,
            "items": items,
            "next_tools": ["acc_reconcile_suggest"],
        }

    def _ai_brief_payment_orders(self, scope, today):
        """Draft payment orders, when the payment order module is installed."""
        if "account.payment.order" not in self.env:
            return None
        Order = self.env["account.payment.order"]
        if not Order.check_access_rights("read", raise_exception=False):
            return {"available": False, "count": None, "items": []}
        domain = [("state", "=", "draft"), ("company_id", "in", scope["company_ids"])]
        orders = Order.search(domain, order="create_date, id")
        has_total = "total_company_currency" in Order._fields
        return {
            "available": True,
            "count": len(orders),
            "items": [
                {
                    "id": order.id,
                    "name": order.name,
                    "company": order.company_id.name,
                    "payment_mode": order.payment_mode_id.name or None,
                    "lines": len(order.payment_line_ids),
                    "total": amount(order.total_company_currency)
                    if has_total
                    else None,
                    "age_days": (today - order.create_date.date()).days,
                }
                for order in orders[:TOP_ITEMS]
            ],
            "next_tools": ["acc_payment_order_prepare"],
        }

    @guarded_tool(
        input_schema={
            "company_id": {
                "type": "integer",
                "minimum": 1,
                "description": "Only this company (res.company id). Default: all "
                "the user's companies.",
            },
            "property_ids": dict(
                PROPERTY_IDS_INPUT,
                description="Only these properties; documents without property "
                "are then left out. Default: all the user's properties and the "
                "documents without property.",
            ),
        },
        output_schema={
            "date": {"type": "string"},
            "worklist": {"type": "array", "items": {"type": "object"}},
            "draft_vendor_bills": {"type": "object"},
            "vendor_bills_to_review": {"type": "object"},
            "duplicate_vendor_bills": {"type": "object"},
            "vendor_bills_to_pay": {"type": "object"},
            "customer_invoices_overdue": {"type": "object"},
            "unapplied_payments": {"type": "object"},
            "draft_payment_orders": {"type": "object"},
        },
        log_result=False,
    )
    def _ai_acc_admin_daily_brief(self, company_id=None, property_ids=None):
        today = fields.Date.context_today(self)
        scope = self._ai_brief_scope(company_id, property_ids)
        result = {
            "date": today,
            "draft_vendor_bills": self._ai_brief_draft_bills(scope, today),
            "vendor_bills_to_review": self._ai_brief_bills_to_review(scope, today),
            "duplicate_vendor_bills": self._ai_brief_duplicates(scope, today),
            "vendor_bills_to_pay": self._ai_brief_payables(scope, today),
            "customer_invoices_overdue": self._ai_brief_customer_overdue(scope, today),
            "unapplied_payments": self._ai_brief_open_payments(scope, today),
        }
        orders = self._ai_brief_payment_orders(scope, today)
        if orders is not None:
            result["draft_payment_orders"] = orders
        to_pay = result["vendor_bills_to_pay"]
        worklist = []
        for key in (
            "draft_vendor_bills",
            "vendor_bills_to_review",
            "duplicate_vendor_bills",
            "vendor_bills_to_pay",
            "customer_invoices_overdue",
            "unapplied_payments",
            "draft_payment_orders",
        ):
            section = result.get(key)
            if not section:
                continue
            if key == "vendor_bills_to_pay":
                count = to_pay["overdue"]["count"] + to_pay["due_soon"]["count"]
            else:
                count = section.get("count") or 0
            if count:
                worklist.append(
                    {
                        "section": key,
                        "count": count,
                        "next_tools": section["next_tools"],
                    }
                )
        result["worklist"] = worklist
        summary = _(
            "Administration brief of %(date)s: %(drafts)s draft vendor bills "
            "(oldest %(oldest)s days), %(review)s to review, %(duplicates)s "
            "groups of probable duplicates, %(overdue)s vendor bills overdue "
            "(%(overdue_amount)s) and %(due_soon)s due in %(days)s days "
            "(%(due_soon_amount)s), %(customer)s customer invoices overdue "
            "(%(customer_amount)s), %(payments)s payments not applied "
            "(%(payments_amount)s)."
        ) % {
            "date": today,
            "drafts": result["draft_vendor_bills"]["count"],
            "oldest": result["draft_vendor_bills"]["oldest_age_days"] or 0,
            "review": result["vendor_bills_to_review"]["count"],
            "duplicates": result["duplicate_vendor_bills"]["count"],
            "overdue": to_pay["overdue"]["count"],
            "overdue_amount": to_pay["overdue"]["pending"],
            "due_soon": to_pay["due_soon"]["count"],
            "days": DUE_SOON_DAYS,
            "due_soon_amount": to_pay["due_soon"]["pending"],
            "customer": result["customer_invoices_overdue"]["count"],
            "customer_amount": result["customer_invoices_overdue"]["pending"],
            "payments": result["unapplied_payments"]["count"],
            "payments_amount": result["unapplied_payments"]["open_amount"],
        }
        if orders and orders["available"]:
            summary += " " + _("%s draft payment orders.") % orders["count"]
        return {
            "summary": summary,
            "period": {"date": today},
            "definitions": [
                _(
                    "Scope: documents of the user's companies and properties, "
                    "plus the ones without property when no property is "
                    "requested. Each section gives its counts, at most %s items "
                    "and next_tools, the tools to act on it; worklist lists the "
                    "sections with something pending."
                )
                % TOP_ITEMS,
                _(
                    "draft_vendor_bills: vendor bills and refunds in draft; "
                    "age_days counts from the day they were created, oldest "
                    "first."
                ),
                _(
                    "vendor_bills_to_review: vendor bills flagged to check (draft "
                    "or posted) and draft ones missing the partner, the bill "
                    "date, the account or the taxes of a line, or without lines."
                ),
                _(
                    "duplicate_vendor_bills: groups of draft or posted vendor "
                    "bills of the same supplier with the same supplier reference, "
                    "or with the same total and bill date; drafts and the bills "
                    "posted in the last %s days are compared, groups with a draft "
                    "first."
                )
                % DUPLICATE_LOOKBACK_DAYS,
                _(
                    "vendor_bills_to_pay: posted vendor bills (not refunds) with "
                    "an amount pending, overdue (due before today) or due in the "
                    "next %s days, by due date."
                )
                % DUE_SOON_DAYS,
                _(
                    "customer_invoices_overdue: posted customer invoices with an "
                    "amount pending and due before today, largest first; "
                    "individuals (guests) are not named."
                ),
                _(
                    "unapplied_payments: posted payments and collections whose "
                    "receivable or payable line is still open, not applied to "
                    "invoices (down payments of future stays included), oldest "
                    "first. Internal transfers are left out."
                ),
                _(
                    "draft_payment_orders: only when the payment order module is "
                    "installed; filtered by company only."
                ),
                _("Sensitive: accounting data."),
            ],
            "result": result,
        }
