from collections import defaultdict

from odoo import _, fields, models
from odoo.exceptions import AccessError

from odoo.addons.pms_ai_tool.tools import guarded_tool

from .common import PROPERTY_IDS_INPUT, amount

MAX_BILLS = 10000
TOP_SUPPLIERS = 30
DEBT_TOLERANCE = 0.005

# Arguments shared by the list and its export.
FILTER_INPUTS = {
    "cutoff": {
        "type": "string",
        "format": "date",
        "description": "Cutoff date (YYYY-MM-DD). Default: today.",
    },
    "property_ids": PROPERTY_IDS_INPUT,
    "company_ids": {
        "type": "array",
        "items": {"type": "integer"},
        "maxItems": 100,
        "description": "Companies to include (ids). Default: all the user's.",
    },
    "supplier": {
        "type": "string",
        "maxLength": 32,
        "description": "Only the bills of one supplier: its numeric partner id "
        "or its VAT (with or without the country prefix).",
    },
    "min_pending": {
        "type": "number",
        "minimum": 0,
        "description": "Only the bills with at least this amount pending at "
        "the cutoff.",
    },
    "include_drafts": {
        "type": "boolean",
        "description": "Also summarise the vendor bills still in draft "
        "(pending validation) dated up to the cutoff. Default true.",
    },
}


def _new_totals():
    return {"bills": 0, "total": 0.0, "paid": 0.0, "pending": 0.0}


def _metrics(totals):
    return {
        "bills": totals["bills"],
        "total": amount(totals["total"]),
        "paid_at_cutoff": amount(totals["paid"]),
        "pending_at_cutoff": amount(totals["pending"]),
    }


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    def _ai_sbp_domain(self, property_ids=None, company_ids=None, supplier=None):
        """Vendor bills of the user's scope narrowed by the tool filters."""
        domain = self._ai_bill_scope_domain(property_ids) + [
            ("move_type", "=", "in_invoice")
        ]
        if company_ids:
            foreign = set(company_ids) - set(self._ai_scope_company_ids())
            if foreign:
                raise AccessError(
                    _("You have no access to the companies %s.")
                    % ", ".join(map(str, sorted(foreign)))
                )
            domain.append(("company_id", "in", list(company_ids)))
        if supplier:
            partner = self._ai_rec_find_partner(supplier)
            domain.append(("commercial_partner_id", "=", partner.id))
        return domain

    def _ai_acc_supplier_bills_pending_candidates(self, cutoff, domain):
        """Posted vendor bills dated up to the cutoff that may be open on it.

        A bill is a candidate when it has an open amount today or when any of
        its reconciliations is dated after the cutoff (the counterpart date is
        the later one of the two lines, as the bill itself is not later).
        """
        return self.env["account.move"].search(
            domain
            + [
                ("state", "=", "posted"),
                ("date", "<=", cutoff),
                "|",
                "|",
                ("amount_residual", ">", 0),
                ("line_ids.matched_debit_ids.max_date", ">", cutoff),
                ("line_ids.matched_credit_ids.max_date", ">", cutoff),
            ],
            order="date, id",
            limit=MAX_BILLS + 1,
        )

    def _ai_supplier_bills_pending_data(self, cutoff, domain, min_pending=None):
        """Vendor bills pending payment at ``cutoff`` and their totals.

        Shared by the list and its export. ``rows`` holds every pending bill
        that passes the filters, the largest pending amount first, and
        ``complete`` is False when there were more than MAX_BILLS candidates.
        """
        candidates = self._ai_acc_supplier_bills_pending_candidates(cutoff, domain)
        complete = len(candidates) <= MAX_BILLS
        candidates = candidates[:MAX_BILLS]
        settlements = self._ai_move_settlements(candidates, cutoff=cutoff)
        rows = []
        totals = _new_totals()
        by_company = defaultdict(_new_totals)
        by_property = defaultdict(_new_totals)
        by_supplier = defaultdict(_new_totals)
        names = {}
        for bill in candidates:
            total = abs(bill.amount_total_signed)
            items = settlements[bill.id]
            paid = sum(item["amount"] for item in items)
            pending = total - paid
            if pending <= DEBT_TOLERANCE or (
                min_pending and amount(pending) < min_pending
            ):
                continue
            company, prop = bill.company_id, bill.pms_property_id
            supplier = bill.commercial_partner_id
            keys = (
                (by_company, company.id),
                (by_property, (company.id, prop.id)),
                (by_supplier, supplier.id),
            )
            names[company.id] = company.name
            names[(company.id, prop.id)] = prop.name or _("No property")
            names[supplier.id] = supplier.name
            for group, key in ((totals, None),) + keys:
                bucket = group if key is None else group[key]
                bucket["bills"] += 1
                bucket["total"] += total
                bucket["paid"] += paid
                bucket["pending"] += pending
            payable = bill.line_ids.filtered(
                lambda line: line.account_id.account_type == "liability_payable"
            ).account_id
            rows.append(
                {
                    "bill_id": bill.id,
                    "bill": bill.name,
                    "supplier_reference": bill.ref or None,
                    "company_id": company.id,
                    "company": company.name,
                    "property_id": prop.id or None,
                    "property": prop.name or None,
                    "journal": bill.journal_id.code,
                    "supplier_id": supplier.id or None,
                    "supplier": supplier.name or None,
                    "supplier_vat": supplier.vat or None,
                    "payable_account": ", ".join(sorted(set(payable.mapped("code"))))
                    or None,
                    "date": bill.date,
                    "due_date": bill.invoice_date_due,
                    "total": amount(total),
                    "paid_at_cutoff": amount(paid),
                    "pending_at_cutoff": amount(pending),
                    "overdue_at_cutoff": bool(
                        bill.invoice_date_due and bill.invoice_date_due < cutoff
                    ),
                    "payments_at_cutoff": len(items),
                    "last_payment_date": items[-1]["date"] if items else None,
                }
            )
        rows.sort(key=lambda r: (-r["pending_at_cutoff"], r["bill_id"]))
        return {
            "rows": rows,
            "complete": complete,
            "totals": dict(_metrics(totals), suppliers=len(by_supplier)),
            "by_company": sorted(
                (
                    {"company_id": key, "company": names[key], **_metrics(value)}
                    for key, value in by_company.items()
                ),
                key=lambda r: (-r["pending_at_cutoff"], r["company"]),
            ),
            "by_property": sorted(
                (
                    {
                        "company_id": key[0],
                        "company": names[key[0]],
                        "property_id": key[1] or None,
                        "property": names[key],
                        **_metrics(value),
                    }
                    for key, value in by_property.items()
                ),
                key=lambda r: (-r["pending_at_cutoff"], r["property"]),
            ),
            "by_supplier": sorted(
                (
                    {
                        "supplier_id": key or None,
                        "supplier": names[key] or None,
                        **_metrics(value),
                    }
                    for key, value in by_supplier.items()
                ),
                key=lambda r: (-r["pending_at_cutoff"], r["supplier"] or ""),
            ),
        }

    def _ai_sbp_incomplete_warning(self):
        return _(
            "INCOMPLETE LIST: more than %(limit)s posted vendor bills may be "
            "pending at the cutoff and only the oldest %(limit)s were analysed, "
            "so the bills, totals and summaries miss the rest. Narrow it with "
            "company_ids, property_ids or supplier."
        ) % {"limit": MAX_BILLS}

    def _ai_sbp_definitions(self):
        return [
            _(
                "Pending bill: posted vendor bill (credit notes are not "
                "counted) with an accounting date on or before the cutoff "
                "and an amount not yet settled on that date."
            ),
            _(
                "paid_at_cutoff: sum of the reconciliations of the bill whose "
                "counterpart entry (payment, bank statement line or credit "
                "note) is dated on or before the cutoff, so partial "
                "payments count by their own date. pending_at_cutoff: the "
                "bill total minus that amount, in the company currency."
            ),
            _(
                "last_payment_date: date of the last of those reconciliations, "
                "empty when nothing was paid by the cutoff. supplier_id, "
                "supplier and supplier_vat: the commercial partner of the bill "
                "(the company, not its contact). payable_account: code of the "
                "payable account of the bill. journal: code of its journal."
            ),
            _(
                "The state of the bill is the current one: a bill posted or "
                "a reconciliation undone after the cutoff is not "
                "reconstructed. Property: the property of the bill; bills "
                "without property are grouped apart."
            ),
            _(
                "Filters: company_ids, property_ids, supplier (the bills of its "
                "commercial partner) and min_pending (the bills with at least "
                "that amount pending at the cutoff). drafts: vendor bills still "
                "in draft (pending validation) dated up to the cutoff, by "
                "company, with the same filters except min_pending."
            ),
            _("Sensitive: accounting data of suppliers."),
        ]

    @guarded_tool(
        input_schema=dict(
            FILTER_INPUTS,
            offset={
                "type": "integer",
                "minimum": 0,
                "description": "Bills to skip: the next_offset of the previous "
                "page. Default 0.",
            },
            limit={
                "type": "integer",
                "minimum": 1,
                "description": "Bills per page. Default and maximum: the row "
                "limit of the AI tools.",
            },
            max_suppliers={
                "type": "integer",
                "minimum": 1,
                "description": "Suppliers in by_supplier, the most pending "
                f"first. Default {TOP_SUPPLIERS}; at most the row limit of the "
                "AI tools.",
            },
        ),
        output_schema={
            "totals": {"type": "object"},
            "complete": {"type": "boolean"},
            "by_company": {"type": "array", "items": {"type": "object"}},
            "by_property": {"type": "array", "items": {"type": "object"}},
            "by_supplier": {"type": "array", "items": {"type": "object"}},
            "bills": {"type": "array", "items": {"type": "object"}},
            "page": {"type": "object"},
            "drafts": {"type": "object"},
        },
        log_result=False,
    )
    def _ai_acc_supplier_bills_pending(
        self,
        cutoff=None,
        property_ids=None,
        company_ids=None,
        supplier=None,
        min_pending=None,
        include_drafts=True,
        offset=0,
        limit=None,
        max_suppliers=None,
    ):
        cutoff = cutoff or fields.Date.context_today(self)
        domain = self._ai_sbp_domain(property_ids, company_ids, supplier)
        data = self._ai_supplier_bills_pending_data(cutoff, domain, min_pending)
        warnings = [] if data["complete"] else [self._ai_sbp_incomplete_warning()]
        rows = data["rows"]
        limit = self._ai_max_rows(limit)
        page = rows[offset : offset + limit]
        has_more = offset + len(page) < len(rows)
        if has_more:
            warnings.append(
                _(
                    "Bills %(first)s to %(last)s of %(total)s are listed: call "
                    "again with offset=%(next)s for the next ones, or export "
                    "the whole list to a file with "
                    "acc_supplier_bills_pending_export."
                )
                % {
                    "first": offset + 1,
                    "last": offset + len(page),
                    "total": len(rows),
                    "next": offset + len(page),
                }
            )
        elif rows and offset >= len(rows):
            warnings.append(
                _("offset %(offset)s is past the last bill (%(total)s bills).")
                % {"offset": offset, "total": len(rows)}
            )
        max_suppliers = self._ai_max_rows(max_suppliers or TOP_SUPPLIERS)
        suppliers = data["by_supplier"]
        if len(suppliers) > max_suppliers:
            warnings.append(
                _(
                    "Only the %(shown)s of %(total)s suppliers with the most "
                    "pending are listed (max_suppliers, at most %(limit)s)."
                )
                % {
                    "shown": max_suppliers,
                    "total": len(suppliers),
                    "limit": self._ai_max_rows(),
                }
            )
        result = {
            "totals": data["totals"],
            "complete": data["complete"],
            "by_company": data["by_company"],
            "by_property": data["by_property"],
            "by_supplier": suppliers[:max_suppliers],
            "bills": page,
            "page": {
                "offset": offset,
                "limit": limit,
                "returned": len(page),
                "total": len(rows),
                "has_more": has_more,
                "next_offset": offset + len(page) if has_more else None,
            },
        }
        if include_drafts:
            result["drafts"] = self._ai_supplier_bill_drafts(cutoff, domain)
        metrics = result["totals"]
        return {
            "summary": _(
                "%(bills)s vendor bills with %(pending)s pending payment on "
                "%(cutoff)s."
            )
            % {
                "bills": metrics["bills"],
                "pending": metrics["pending_at_cutoff"],
                "cutoff": cutoff,
            },
            "period": {"cutoff": cutoff},
            "definitions": self._ai_sbp_definitions()
            + [
                _(
                    "bills: one page of the pending bills, the most pending "
                    "first; page gives its offset, the total number of bills "
                    "and next_offset while there are more. totals, by_company, "
                    "by_property and by_supplier cover every bill that passes "
                    "the filters. complete: false when the list is cut (see "
                    "the warnings)."
                ),
            ],
            "warnings": warnings,
            "result": result,
        }

    def _ai_supplier_bill_drafts(self, cutoff, domain):
        groups = self.env["account.move"].read_group(
            domain
            + [
                ("state", "=", "draft"),
                ("date", "<=", cutoff),
            ],
            ["amount_total_signed:sum"],
            ["company_id"],
            lazy=False,
        )
        rows = [
            {
                "company_id": group["company_id"][0],
                "company": group["company_id"][1],
                "bills": group["__count"],
                "total": amount(abs(group["amount_total_signed"])),
            }
            for group in groups
        ]
        return {
            "bills": sum(row["bills"] for row in rows),
            "total": amount(sum(row["total"] for row in rows)),
            "by_company": rows,
        }
