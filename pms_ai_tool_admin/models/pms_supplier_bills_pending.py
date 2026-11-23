from collections import defaultdict

from odoo import _, fields, models

from odoo.addons.pms_ai_tool.tools import guarded_tool

from .common import PROPERTY_IDS_INPUT, amount

MAX_BILLS = 3000
TOP_SUPPLIERS = 30
DEBT_TOLERANCE = 0.005


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

    def _ai_acc_supplier_bills_pending_candidates(self, cutoff, property_ids):
        """Posted vendor bills dated up to the cutoff that may be open on it.

        A bill is a candidate when it has an open amount today or when any of
        its reconciliations is dated after the cutoff (the counterpart date is
        the later one of the two lines, as the bill itself is not later).
        """
        domain = self._ai_bill_scope_domain(property_ids) + [
            ("move_type", "=", "in_invoice"),
            ("state", "=", "posted"),
            ("date", "<=", cutoff),
            "|",
            "|",
            ("amount_residual", ">", 0),
            ("line_ids.matched_debit_ids.max_date", ">", cutoff),
            ("line_ids.matched_credit_ids.max_date", ">", cutoff),
        ]
        return self.env["account.move"].search(
            domain, order="date, id", limit=MAX_BILLS + 1
        )

    @guarded_tool(
        input_schema={
            "cutoff": {
                "type": "string",
                "format": "date",
                "description": "Cutoff date (YYYY-MM-DD). Default: today.",
            },
            "property_ids": PROPERTY_IDS_INPUT,
            "include_drafts": {
                "type": "boolean",
                "description": "Also summarise the vendor bills still in draft "
                "(pending validation) dated up to the cutoff. Default true.",
            },
        },
        output_schema={
            "totals": {"type": "object"},
            "by_company": {"type": "array", "items": {"type": "object"}},
            "by_property": {"type": "array", "items": {"type": "object"}},
            "by_supplier": {"type": "array", "items": {"type": "object"}},
            "bills": {"type": "array", "items": {"type": "object"}},
            "drafts": {"type": "object"},
        },
        log_result=False,
    )
    def _ai_acc_supplier_bills_pending(
        self, cutoff=None, property_ids=None, include_drafts=True
    ):
        cutoff = cutoff or fields.Date.context_today(self)
        candidates = self._ai_acc_supplier_bills_pending_candidates(
            cutoff, property_ids
        )
        warnings = []
        if len(candidates) > MAX_BILLS:
            candidates = candidates[:MAX_BILLS]
            warnings.append(
                _("Only the oldest %s candidate bills were analysed.") % MAX_BILLS
            )
        settlements = self._ai_move_settlements(candidates, cutoff=cutoff)
        rows = []
        totals = _new_totals()
        by_company = defaultdict(_new_totals)
        by_property = defaultdict(_new_totals)
        by_supplier = defaultdict(_new_totals)
        names = {}
        for bill in candidates:
            total = abs(bill.amount_total_signed)
            paid = sum(item["amount"] for item in settlements[bill.id])
            pending = total - paid
            if pending <= DEBT_TOLERANCE:
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
            rows.append(
                {
                    "bill_id": bill.id,
                    "bill": bill.name,
                    "supplier_reference": bill.ref or None,
                    "company": company.name,
                    "property_id": prop.id or None,
                    "property": prop.name or None,
                    "supplier": supplier.name,
                    "date": bill.date,
                    "due_date": bill.invoice_date_due,
                    "total": amount(total),
                    "paid_at_cutoff": amount(paid),
                    "pending_at_cutoff": amount(pending),
                    "overdue_at_cutoff": bool(
                        bill.invoice_date_due and bill.invoice_date_due < cutoff
                    ),
                    "payments_at_cutoff": len(settlements[bill.id]),
                }
            )
        rows.sort(key=lambda r: (-r["pending_at_cutoff"], r["bill_id"]))
        shown = self._ai_limit_rows(rows, warnings)
        supplier_rows = sorted(
            (
                {"supplier_id": key, "supplier": names[key], **_metrics(value)}
                for key, value in by_supplier.items()
            ),
            key=lambda r: (-r["pending_at_cutoff"], r["supplier"]),
        )
        if len(supplier_rows) > TOP_SUPPLIERS:
            warnings.append(
                _("Only the %(shown)s suppliers with the most pending are listed.")
                % {"shown": TOP_SUPPLIERS}
            )
        result = {
            "totals": _metrics(totals),
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
            "by_supplier": supplier_rows[:TOP_SUPPLIERS],
            "bills": shown,
        }
        if include_drafts:
            result["drafts"] = self._ai_supplier_bill_drafts(cutoff, property_ids)
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
            "definitions": [
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
                    "The state of the bill is the current one: a bill posted or "
                    "a reconciliation undone after the cutoff is not "
                    "reconstructed. Property: the property of the bill; bills "
                    "without property are grouped apart."
                ),
                _(
                    "drafts: vendor bills still in draft (pending validation) "
                    "dated up to the cutoff, by company."
                ),
                _("Sensitive: accounting data of suppliers."),
            ],
            "warnings": warnings,
            "result": result,
        }

    def _ai_supplier_bill_drafts(self, cutoff, property_ids):
        groups = self.env["account.move"].read_group(
            self._ai_bill_scope_domain(property_ids)
            + [
                ("move_type", "=", "in_invoice"),
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
