from collections import defaultdict

from odoo import _, models
from odoo.exceptions import UserError

from odoo.addons.pms_ai_tool.tools import guarded_tool, truncate

from .common import DATE_FROM_INPUT, MAX_DAYS, PROPERTY_IDS_INPUT, amount

MAX_BILLS = 2000
LABEL_CHARS = 90
DEBT_TOLERANCE = 0.005
LINE_GROUPS = ("supplier", "account", "month", "property")
PAYMENT_GROUPS = ("supplier", "property", "method", "account", "month")
NO_PAYMENT_KINDS = ("credit_note", "invoice", "down_payment_invoice")


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    def _ai_supplier_invoice_domain(
        self,
        date_from,
        date_to,
        property_ids,
        account_prefixes,
        supplier,
        min_amount,
        max_amount,
        text,
    ):
        domain = self._ai_bill_scope_domain(property_ids) + [
            ("move_type", "=", "in_invoice"),
            ("state", "=", "posted"),
            ("date", ">=", date_from),
            ("date", "<=", date_to),
        ]
        if account_prefixes:
            domain += ["|"] * (len(account_prefixes) - 1)
            domain += [
                ("invoice_line_ids.account_id.code", "=like", f"{prefix}%")
                for prefix in account_prefixes
            ]
        if supplier:
            domain.append(("commercial_partner_id.name", "ilike", supplier))
        if min_amount is not None:
            domain.append(("amount_total", ">=", min_amount))
        if max_amount is not None:
            domain.append(("amount_total", "<=", max_amount))
        if text:
            domain += [
                "|",
                "|",
                "|",
                ("name", "ilike", text),
                ("ref", "ilike", text),
                ("commercial_partner_id.name", "ilike", text),
                ("invoice_line_ids.name", "ilike", text),
            ]
        return domain

    def _ai_bill_payment_method(self, row):
        """Group label of the real payment method of a settlement row."""
        journal_type = row["journal_type"]
        if row["kind"] == "payment":
            return f"{journal_type}: {row['payment_method'] or '-'}"
        if row["kind"] == "bank_statement":
            return f"{journal_type}: bank statement line, method not identified"
        return f"{journal_type}: other entry"

    @guarded_tool(
        input_schema={
            "date_from": dict(
                DATE_FROM_INPUT, description="First accounting date (YYYY-MM-DD)."
            ),
            "date_to": {
                "type": "string",
                "format": "date",
                "description": "Last accounting date, included (YYYY-MM-DD). "
                f"At most {MAX_DAYS} days after date_from.",
            },
            "property_ids": PROPERTY_IDS_INPUT,
            "account_prefixes": {
                "type": "array",
                "items": {"type": "string", "maxLength": 12},
                "maxItems": 20,
                "description": "Account code prefixes (for example 601 or 62): "
                "only the lines on those accounts are analysed.",
            },
            "supplier": {
                "type": "string",
                "maxLength": 80,
                "description": "Part of the supplier name.",
            },
            "min_amount": {"type": "number", "description": "Minimum bill total."},
            "max_amount": {"type": "number", "description": "Maximum bill total."},
            "text": {
                "type": "string",
                "maxLength": 80,
                "description": "Text in the bill number, reference, supplier or "
                "line descriptions.",
            },
            "group_lines_by": {
                "type": "string",
                "enum": list(LINE_GROUPS),
                "description": "Grouping of the posted lines. Default: supplier.",
            },
            "include_payments": {
                "type": "boolean",
                "description": "Analyse how the bills were paid. Default true.",
            },
            "group_payments_by": {
                "type": "string",
                "enum": list(PAYMENT_GROUPS),
                "description": "Grouping of the payment analysis. Default: supplier.",
            },
        },
        output_schema={
            "totals": {"type": "object"},
            "line_groups": {"type": "array", "items": {"type": "object"}},
            "payment_groups": {"type": "array", "items": {"type": "object"}},
            "bills": {"type": "array", "items": {"type": "object"}},
            "lines": {"type": "array", "items": {"type": "object"}},
        },
        required_inputs=["date_from", "date_to"],
        log_result=False,
    )
    def _ai_acc_supplier_invoices(
        self,
        date_from,
        date_to,
        property_ids=None,
        account_prefixes=None,
        supplier=None,
        min_amount=None,
        max_amount=None,
        text=None,
        group_lines_by="supplier",
        include_payments=True,
        group_payments_by="supplier",
    ):
        period = self._ai_check_period(date_from, date_to, MAX_DAYS)
        prefixes = [p.strip() for p in account_prefixes or [] if p and p.strip()]
        if any(not prefix.isalnum() for prefix in prefixes):
            raise UserError(_("Account prefixes must be alphanumeric."))
        domain = self._ai_supplier_invoice_domain(
            date_from,
            date_to,
            property_ids,
            prefixes,
            supplier,
            min_amount,
            max_amount,
            text,
        )
        bills = self.env["account.move"].search(
            domain, order="date, id", limit=MAX_BILLS + 1
        )
        warnings = []
        if len(bills) > MAX_BILLS:
            bills = bills[:MAX_BILLS]
            warnings.append(
                _("Only the first %s bills of the period were analysed.") % MAX_BILLS
            )
        lines = bills.invoice_line_ids.filtered(
            lambda ln: ln.display_type == "product"
            and (
                not prefixes
                or any(ln.account_id.code.startswith(prefix) for prefix in prefixes)
            )
        )
        line_groups, line_totals = self._ai_supplier_line_groups(lines, group_lines_by)
        main_account = {}
        for bill in bills:
            bill_lines = lines.filtered(lambda ln, b=bill: ln.move_id == b)
            if bill_lines:
                best = max(bill_lines, key=lambda ln: abs(ln.price_subtotal))
                main_account[bill.id] = best.account_id
        settlements = self._ai_move_settlements(bills) if include_payments else {}
        bill_rows = [
            self._ai_supplier_bill_row(
                bill, settlements.get(bill.id, []), include_payments
            )
            for bill in bills
        ]
        result = {
            "totals": {
                "bills": len(bills),
                "lines": len(lines),
                **line_totals,
                "bill_total": amount(sum(bills.mapped("amount_total_signed"))),
            },
            "line_groups": line_groups,
            "bills": self._ai_limit_rows(bill_rows, warnings),
            "lines": [
                {
                    "bill": ln.move_id.name,
                    "supplier": ln.move_id.commercial_partner_id.name,
                    "date": ln.move_id.date,
                    "property": ln.move_id.pms_property_id.name or None,
                    "account": f"{ln.account_id.code} {ln.account_id.name}",
                    "label": truncate(ln.name, LABEL_CHARS),
                    "untaxed": amount(ln.price_subtotal),
                    "tax": amount(ln.price_total - ln.price_subtotal),
                }
                for ln in self._ai_limit_rows(
                    sorted(lines, key=lambda ln: (-abs(ln.price_subtotal), ln.id)),
                    warnings,
                )
            ],
        }
        if include_payments:
            result["payment_groups"] = self._ai_supplier_payment_groups(
                bills, settlements, group_payments_by, main_account
            )
        return {
            "summary": _(
                "%(bills)s posted vendor bills from %(from)s to %(to)s, %(untaxed)s "
                "untaxed in the analysed lines."
            )
            % {
                "bills": len(bills),
                "from": date_from,
                "to": date_to,
                "untaxed": result["totals"]["untaxed"],
            },
            "period": period,
            "definitions": [
                _(
                    "Bills: posted vendor bills (credit notes are not included) "
                    "with an accounting date in the period, of the properties in "
                    "scope; bills without property are included. When account "
                    "prefixes are given, only the lines on those accounts are "
                    "analysed, but the bill totals are the whole bill."
                ),
                _(
                    "Lines: the product lines of the bills, with untaxed amount "
                    "and tax, grouped by supplier, account, month or property."
                ),
                _(
                    "Payments: the reconciliations of the bill, dated by the "
                    "accounting date of the payment. The real method is the "
                    "payment method and journal type of the payment. When the "
                    "bill was reconciled directly with a bank statement line "
                    "there is no payment record: only the journal type is known "
                    "and the method is reported as not identified."
                ),
                _(
                    "real_days: days from the bill date to the payment date, "
                    "weighted by the amount of each payment. late_pct: share of "
                    "the paid amount settled after the due date. delay_days of a "
                    "payment: its date minus the due date (negative: early). "
                    "Credit notes applied to a bill are shown apart and are not "
                    "payments. Account of a bill in the payment analysis: the "
                    "account of its largest analysed line."
                ),
                _(
                    "paid and pending are as of today, not at the end of the "
                    "period. Attachments (PDF) are not read."
                ),
                _("Sensitive: accounting data of suppliers."),
            ],
            "warnings": warnings,
            "result": result,
        }

    def _ai_supplier_group_key(self, group_by, bill, account=None):
        if group_by == "supplier":
            return bill.commercial_partner_id.id, bill.commercial_partner_id.name
        if group_by == "property":
            return bill.pms_property_id.id, bill.pms_property_id.name or _(
                "No property"
            )
        if group_by == "month":
            month = bill.date.strftime("%Y-%m")
            return month, month
        return (
            account.id if account else 0,
            f"{account.code} {account.name}" if account else _("No account"),
        )

    def _ai_supplier_line_groups(self, lines, group_by):
        groups = defaultdict(lambda: {"untaxed": 0.0, "tax": 0.0, "lines": 0})
        labels = {}
        for ln in lines:
            key, label = self._ai_supplier_group_key(
                group_by, ln.move_id, ln.account_id
            )
            labels[key] = label
            group = groups[key]
            group["untaxed"] += ln.price_subtotal
            group["tax"] += ln.price_total - ln.price_subtotal
            group["lines"] += 1
        rows = sorted(
            (
                {
                    "group_by": group_by,
                    "key": key,
                    "label": labels[key],
                    "untaxed": amount(group["untaxed"]),
                    "tax": amount(group["tax"]),
                    "total": amount(group["untaxed"] + group["tax"]),
                    "lines": group["lines"],
                }
                for key, group in groups.items()
            ),
            key=lambda r: (-abs(r["untaxed"]), str(r["label"])),
        )
        totals = {
            "untaxed": amount(sum(ln.price_subtotal for ln in lines)),
            "tax": amount(sum(ln.price_total - ln.price_subtotal for ln in lines)),
        }
        return rows, totals

    def _ai_supplier_bill_row(self, bill, items, include_payments):
        row = {
            "bill_id": bill.id,
            "bill": bill.name,
            "supplier_reference": bill.ref or None,
            "supplier": bill.commercial_partner_id.name,
            "property": bill.pms_property_id.name or None,
            "date": bill.date,
            "due_date": bill.invoice_date_due,
            "payment_term": bill.invoice_payment_term_id.name or None,
            "total": amount(abs(bill.amount_total_signed)),
            "untaxed": amount(bill.amount_untaxed),
        }
        if not include_payments:
            return row
        paid = [
            i
            for i in items
            if self._ai_settlement_kind(i["counterpart"]) not in NO_PAYMENT_KINDS
        ]
        credited = [i for i in items if i not in paid]
        paid_total = sum(i["amount"] for i in paid)
        credited_total = sum(i["amount"] for i in credited)
        pending = abs(bill.amount_total_signed) - paid_total - credited_total
        row.update(
            {
                "paid": amount(paid_total),
                "credited": amount(credited_total),
                "pending": amount(pending if pending > DEBT_TOLERANCE else 0),
                "payments": [self._ai_settlement_row(i) for i in paid],
                "split_payment": len(paid) > 1,
                "no_payment_record": any(
                    self._ai_settlement_kind(i["counterpart"]) == "bank_statement"
                    for i in paid
                ),
            }
        )
        if paid:
            last = paid[-1]["date"]
            row["real_days_to_last_payment"] = (last - bill.date).days
            if bill.invoice_date_due:
                row["delay_days_vs_due"] = (last - bill.invoice_date_due).days
        return row

    def _ai_supplier_payment_groups(self, bills, settlements, group_by, main_account):
        groups = defaultdict(
            lambda: {
                "bills": set(),
                "total": 0.0,
                "paid": 0.0,
                "pending": 0.0,
                "weighted_days": 0.0,
                "late": 0.0,
            }
        )
        labels = {}
        for bill in bills:
            key, label = self._ai_supplier_group_key(
                group_by, bill, main_account.get(bill.id)
            )
            items = [
                i
                for i in settlements.get(bill.id, [])
                if self._ai_settlement_kind(i["counterpart"]) not in NO_PAYMENT_KINDS
            ]
            total = abs(bill.amount_total_signed)
            if group_by == "method":
                keyed = defaultdict(list)
                for i in items:
                    row = self._ai_settlement_row(i)
                    keyed[self._ai_bill_payment_method(row)].append(i)
                if not keyed:
                    keyed[_("Not paid")] = []
                targets = keyed.items()
            else:
                labels[key] = label
                targets = [(key, items)]
            for target, target_items in targets:
                if group_by == "method":
                    labels[target] = target
                group = groups[target]
                group["bills"].add(bill.id)
                for i in target_items:
                    group["paid"] += i["amount"]
                    group["weighted_days"] += i["amount"] * (i["date"] - bill.date).days
                    if bill.invoice_date_due and i["date"] > bill.invoice_date_due:
                        group["late"] += i["amount"]
            if group_by != "method":
                group = groups[key]
                group["total"] += total
                group["pending"] += max(
                    total - sum(i["amount"] for i in settlements.get(bill.id, [])), 0
                )
        return sorted(
            (
                {
                    "group_by": group_by,
                    "key": key,
                    "label": labels[key],
                    "bills": len(group["bills"]),
                    "paid": amount(group["paid"]),
                    **(
                        {
                            "total": amount(group["total"]),
                            "pending": amount(
                                group["pending"]
                                if group["pending"] > DEBT_TOLERANCE
                                else 0
                            ),
                        }
                        if group_by != "method"
                        else {}
                    ),
                    "real_days": amount(group["weighted_days"] / group["paid"])
                    if group["paid"]
                    else None,
                    "late_pct": amount(100 * group["late"] / group["paid"])
                    if group["paid"]
                    else None,
                }
                for key, group in groups.items()
            ),
            key=lambda r: (-r["paid"], str(r["label"])),
        )
