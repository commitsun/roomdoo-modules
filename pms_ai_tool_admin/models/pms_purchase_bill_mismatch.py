from collections import defaultdict

from odoo import _, models
from odoo.exceptions import UserError

from odoo.addons.pms_ai_tool.tools import guarded_tool

from .common import DATE_FROM_INPUT, MAX_DAYS, PROPERTY_IDS_INPUT, amount

MAX_BILLS = 2000
DEFAULT_TOLERANCE = 0.05
OPEN_PAYMENT_STATES = ("not_paid", "partial")


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    def _ai_po_billed_amount(self, order, bills_by_order):
        """Amount of the posted bills of a purchase order, in its currency.

        A bill that only belongs to this order counts whole (so extra lines such
        as freight show up as a difference); a bill shared with other orders
        counts only through its lines linked to this order.
        Returns ``(amount, shared_bills)``.
        """
        total, shared = 0.0, False
        for bill in bills_by_order[order.id]:
            sign = -1 if bill.move_type == "in_refund" else 1
            linked = bill.invoice_line_ids.filtered("purchase_line_id")
            if linked.purchase_line_id.order_id == order:
                value = bill.amount_total
            else:
                value = sum(
                    line.price_total
                    for line in linked
                    if line.purchase_line_id.order_id == order
                )
                shared = True
            total += sign * bill.currency_id._convert(
                value,
                order.currency_id,
                bill.company_id,
                bill.invoice_date or bill.date,
            )
        return total, shared

    @guarded_tool(
        input_schema={
            "date_from": dict(
                DATE_FROM_INPUT, description="First bill date (YYYY-MM-DD)."
            ),
            "date_to": {
                "type": "string",
                "format": "date",
                "description": "Last bill date, included (YYYY-MM-DD). "
                f"At most {MAX_DAYS} days after date_from.",
            },
            "property_ids": PROPERTY_IDS_INPUT,
            "min_difference": {
                "type": "number",
                "description": "Smallest difference reported, in the order "
                f"currency. Default {DEFAULT_TOLERANCE}.",
            },
            "include_partially_billed": {
                "type": "boolean",
                "description": "Also compare the orders that Odoo does not "
                "consider fully billed. Default false.",
            },
        },
        output_schema={
            "totals": {"type": "object"},
            "by_company": {"type": "array", "items": {"type": "object"}},
            "by_property": {"type": "array", "items": {"type": "object"}},
            "mismatches": {"type": "array", "items": {"type": "object"}},
            "bills_by_state": {"type": "array", "items": {"type": "object"}},
        },
        required_inputs=["date_from", "date_to"],
        log_result=False,
    )
    def _ai_acc_purchase_bill_mismatch(
        self,
        date_from,
        date_to,
        property_ids=None,
        min_difference=DEFAULT_TOLERANCE,
        include_partially_billed=False,
    ):
        if "purchase.order" not in self.env:
            raise UserError(_("The purchase module is not installed."))
        period = self._ai_check_period(date_from, date_to, MAX_DAYS)
        scope = self._ai_bill_scope_domain(property_ids)
        bills = self.env["account.move"].search(
            scope
            + [
                ("move_type", "in", ("in_invoice", "in_refund")),
                ("state", "=", "posted"),
                ("date", ">=", date_from),
                ("date", "<=", date_to),
                ("invoice_line_ids.purchase_line_id", "!=", False),
            ],
            order="date, id",
            limit=MAX_BILLS + 1,
        )
        warnings = []
        if len(bills) > MAX_BILLS:
            bills = bills[:MAX_BILLS]
            warnings.append(
                _("Only the first %s bills of the period were analysed.") % MAX_BILLS
            )
        # Orders and their bills are read with sudo: the user may have no
        # purchase rights, and the scope is already given by the bills found.
        orders = bills.sudo().invoice_line_ids.purchase_line_id.order_id
        bills_by_order = defaultdict(lambda: self.env["account.move"].sudo())
        for order in orders:
            bills_by_order[order.id] = order.invoice_ids.filtered(
                lambda m: m.state == "posted"
                and m.move_type in ("in_invoice", "in_refund")
            )
        period_bills = defaultdict(lambda: self.env["account.move"].sudo())
        for bill in bills.sudo():
            for order in bill.invoice_line_ids.purchase_line_id.order_id:
                period_bills[order.id] |= bill
        rows, by_company, by_property = [], {}, {}
        for order in orders.sorted("id"):
            if order.invoice_status != "invoiced" and not include_partially_billed:
                continue
            billed, shared = self._ai_po_billed_amount(order, bills_by_order)
            difference = billed - order.amount_total
            if abs(difference) <= min_difference:
                continue
            found = period_bills[order.id]
            props = found.pms_property_id
            row = {
                "order_id": order.id,
                "order": order.name,
                "company": order.company_id.name,
                "property": ", ".join(props.mapped("name")) or None,
                "supplier": order.partner_id.name,
                "bills": found.mapped("name"),
                "currency": order.currency_id.name,
                "order_total": amount(order.amount_total),
                "billed_total": amount(billed),
                "difference": amount(difference),
                "fully_billed": order.invoice_status == "invoiced",
                "shared_bills": shared,
            }
            rows.append(row)
            for group, key in (
                (by_company, row["company"]),
                (by_property, (row["company"], row["property"] or _("No property"))),
            ):
                counters = group.setdefault(key, {"orders": 0, "difference": 0.0})
                counters["orders"] += 1
                counters["difference"] += difference
        rows.sort(key=lambda r: (-abs(r["difference"]), r["order_id"]))
        net = sum(row["difference"] for row in rows)
        return {
            "summary": _(
                "%(count)s purchase orders differ from their vendor bills by more "
                "than %(limit)s from %(from)s to %(to)s (net %(net)s)."
            )
            % {
                "count": len(rows),
                "limit": min_difference,
                "from": date_from,
                "to": date_to,
                "net": amount(net),
            },
            "period": period,
            "definitions": [
                _(
                    "Compared: purchase orders (confirmed) that have a posted vendor "
                    "bill dated in the period. billed_total: the posted vendor bills "
                    "of the order (credit notes subtract), in the order currency, "
                    "including bills outside the period. difference: billed_total "
                    "minus order_total."
                ),
                _(
                    "By default only the orders Odoo considers fully billed are "
                    "compared, because a partly billed order differs from its bills "
                    "on purpose. A bill shared by several orders counts through its "
                    "lines linked to each order (shared_bills)."
                ),
                _(
                    "Property: the property of the bills in the period. "
                    "bills_by_state: vendor bills in draft (pending validation) and "
                    "posted bills not "
                    "yet paid or partly paid, up to the end of the period, by company."
                ),
                _("Sensitive: accounting data of suppliers."),
            ],
            "warnings": warnings,
            "result": {
                "totals": {
                    "orders": len(rows),
                    "net_difference": amount(net),
                    "absolute_difference": amount(
                        sum(abs(row["difference"]) for row in rows)
                    ),
                },
                "by_company": [
                    {
                        "company": key,
                        "orders": value["orders"],
                        "net_difference": amount(value["difference"]),
                    }
                    for key, value in sorted(by_company.items())
                ],
                "by_property": [
                    {
                        "company": key[0],
                        "property": key[1],
                        "orders": value["orders"],
                        "net_difference": amount(value["difference"]),
                    }
                    for key, value in sorted(by_property.items())
                ],
                "mismatches": self._ai_limit_rows(rows, warnings),
                "bills_by_state": self._ai_bills_by_state(scope, date_to),
            },
        }

    def _ai_bills_by_state(self, scope, date_to):
        groups = self.env["account.move"].read_group(
            scope
            + [
                ("move_type", "=", "in_invoice"),
                ("date", "<=", date_to),
                "|",
                ("state", "=", "draft"),
                "&",
                ("state", "=", "posted"),
                ("payment_state", "in", OPEN_PAYMENT_STATES),
            ],
            ["amount_total_signed:sum"],
            ["company_id", "state", "payment_state"],
            lazy=False,
        )
        return sorted(
            (
                {
                    "company": group["company_id"][1],
                    "state": group["state"]
                    if group["state"] == "draft"
                    else group["payment_state"],
                    "bills": group["__count"],
                    "total": amount(abs(group["amount_total_signed"])),
                }
                for group in groups
            ),
            key=lambda r: (r["company"], r["state"]),
        )
