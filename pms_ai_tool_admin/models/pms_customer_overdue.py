from collections import defaultdict
from datetime import timedelta

from odoo import _, fields, models

from odoo.addons.pms_ai_tool.tools import guarded_tool

from .common import PROPERTY_IDS_INPUT, amount

TOP_DEBTORS = 20
# (key, first day overdue, last day overdue)
BUCKETS = (
    ("1_30", 1, 30),
    ("31_60", 31, 60),
    ("61_90", 61, 90),
    ("over_90", 91, None),
)


def _new_buckets():
    return {key: {"amount": 0.0, "invoices": 0} for key, _low, _high in BUCKETS}


def _metrics(buckets):
    return {
        "overdue_amount": amount(sum(b["amount"] for b in buckets.values())),
        "invoices": sum(b["invoices"] for b in buckets.values()),
        "buckets": {
            key: {"amount": amount(b["amount"]), "invoices": b["invoices"]}
            for key, b in buckets.items()
        },
    }


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    def _ai_overdue_domain(self, as_of, property_ids, partner_kind):
        """Open posted customer invoices due before ``as_of``, in scope."""
        domain = [
            ("move_type", "=", "out_invoice"),
            ("state", "=", "posted"),
            ("amount_residual_signed", ">", 0),
            ("invoice_date_due", "<", as_of),
            ("company_id", "in", self._ai_scope_company_ids()),
        ]
        if property_ids:
            domain.append(
                ("pms_property_id", "in", self._ai_scope_property_ids(property_ids))
            )
        else:
            domain += [
                "|",
                ("pms_property_id", "=", False),
                ("pms_property_id", "in", self._ai_scope_property_ids()),
            ]
        if partner_kind == "agency":
            domain.append(("commercial_partner_id.is_agency", "=", True))
        elif partner_kind == "company":
            domain += [
                ("commercial_partner_id.is_company", "=", True),
                ("commercial_partner_id.is_agency", "=", False),
            ]
        return domain

    @guarded_tool(
        input_schema={
            "as_of": {
                "type": "string",
                "format": "date",
                "description": "Date to measure the overdue on (YYYY-MM-DD). "
                "Default: today.",
            },
            "property_ids": PROPERTY_IDS_INPUT,
            "partner_kind": {
                "type": "string",
                "enum": ["all", "agency", "company"],
                "description": "Kind of customer: agencies, companies that are "
                "not agencies, or all of them (default).",
            },
        },
        output_schema={
            "totals": {"type": "object"},
            "by_property": {"type": "array", "items": {"type": "object"}},
            "top_debtors": {"type": "array", "items": {"type": "object"}},
        },
        log_result=False,
    )
    def _ai_pms_customer_overdue(
        self, as_of=None, property_ids=None, partner_kind="all"
    ):
        as_of = as_of or fields.Date.context_today(self)
        domain = self._ai_overdue_domain(as_of, property_ids, partner_kind)
        moves = self.env["account.move"]
        totals = _new_buckets()
        by_property = defaultdict(_new_buckets)
        for key, low, high in BUCKETS:
            bucket_domain = [("invoice_date_due", "<=", as_of - timedelta(days=low))]
            if high:
                bucket_domain.append(
                    ("invoice_date_due", ">=", as_of - timedelta(days=high))
                )
            for group in moves.read_group(
                domain + bucket_domain,
                ["amount_residual_signed:sum"],
                ["pms_property_id"],
                lazy=False,
            ):
                property_id = group["pms_property_id"] and group["pms_property_id"][0]
                for buckets in (totals, by_property[property_id or False]):
                    buckets[key]["amount"] += group["amount_residual_signed"]
                    buckets[key]["invoices"] += group["__count"]
        names = {
            prop.id: prop.name
            for prop in self.env["pms.property"].browse(
                [property_id for property_id in by_property if property_id]
            )
        }
        property_rows = sorted(
            (
                {
                    "property_id": property_id or None,
                    "property": names.get(property_id) or _("No property"),
                    **_metrics(buckets),
                }
                for property_id, buckets in by_property.items()
            ),
            key=lambda r: (-r["overdue_amount"], r["property"]),
        )
        debtors = moves.read_group(
            domain,
            ["amount_residual_signed:sum", "invoice_date_due:min"],
            ["commercial_partner_id"],
            orderby="amount_residual_signed desc",
            limit=TOP_DEBTORS,
            lazy=False,
        )
        partners = {
            partner.id: partner
            for partner in self.env["res.partner"].browse(
                [g["commercial_partner_id"][0] for g in debtors]
            )
        }
        anonymous = self.env.ref("pms.various_pms_partner", raise_if_not_found=False)
        debtor_rows = []
        for group in debtors:
            partner = partners[group["commercial_partner_id"][0]]
            debtor_rows.append(
                {
                    "partner_id": partner.id,
                    "partner": partner.name,
                    "kind": "agency"
                    if partner.is_agency
                    else "company"
                    if partner.is_company
                    else "person",
                    "anonymous_customer": partner == anonymous,
                    "overdue_amount": amount(group["amount_residual_signed"]),
                    "invoices": group["__count"],
                    "oldest_due_date": group["invoice_date_due"],
                    "max_days_overdue": (as_of - group["invoice_date_due"]).days,
                }
            )
        totals_metrics = _metrics(totals)
        return {
            "summary": _("%(invoices)s overdue invoices for %(amount)s on %(date)s.")
            % {
                "invoices": totals_metrics["invoices"],
                "amount": totals_metrics["overdue_amount"],
                "date": as_of,
            },
            "period": {"as_of": as_of},
            "definitions": [
                _(
                    "Overdue invoice: posted customer invoice (credit notes are "
                    "not counted) with an open amount and a due date before as_of. "
                    "The amount is what is still open, in the company currency, "
                    "without netting payments or credit notes of the same "
                    "customer that were not applied to the invoice."
                ),
                _(
                    "Aging: days between the due date and as_of, in the buckets "
                    "1_30, 31_60, 61_90 and over_90. An invoice with several due "
                    "dates is aged by its last one."
                ),
                _(
                    "Property: the property of the invoice; invoices without "
                    "property are grouped apart. partner_kind filters by the "
                    "commercial partner of the invoice: agency (marked as "
                    "agency), company (a company that is not an agency) or all."
                ),
                _(
                    "top_debtors: the %s customers with the largest open amount. "
                    "anonymous_customer marks the shared customer of simplified "
                    "invoices."
                )
                % TOP_DEBTORS,
                _("Sensitive: accounting data of customers."),
            ],
            "warnings": [],
            "result": {
                "totals": totals_metrics,
                "by_property": property_rows,
                "top_debtors": debtor_rows,
            },
        }
