import re
from collections import Counter
from datetime import date as date_cls

from odoo import _, api, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.pms_ai_tool.tools import guarded_tool

from .common import amount

PAYORDER_MODEL = "account.payment.order"
PAYORDER_GROUP = "account_payment_order.group_account_payment"
PAYORDER_MAX_BILLS = 50
PAYORDER_MAX_ID_DIGITS = 12
# Open payment lines in these states keep a bill out of another order, as in
# the wizard of the module.
PAYORDER_BUSY_STATES = ("draft", "open", "generated")


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    @api.model
    def _ai_acc_payorder_find_mode(self, name, company_id):
        domain = [
            ("name", "=ilike", name.strip()),
            ("payment_type", "=", "outbound"),
            ("payment_order_ok", "=", True),
            ("company_id", "in", self._ai_scope_company_ids()),
        ]
        if company_id:
            domain.append(("company_id", "=", company_id))
        modes = self.env["account.payment.mode"].search(domain, limit=2)
        if not modes:
            raise UserError(
                _("No outbound payment mode with payment orders named %s.") % name
            )
        if len(modes) > 1:
            raise UserError(
                _("Several payment modes are named %s: give the company_id.") % name
            )
        return modes

    @api.model
    def _ai_acc_payorder_find_order(self, reference):
        reference = reference.strip()
        if re.fullmatch(rf"\d{{1,{PAYORDER_MAX_ID_DIGITS}}}", reference):
            criterion = ("id", "=", int(reference))
        else:
            criterion = ("name", "=", reference)
        orders = self.env[PAYORDER_MODEL].search(
            [
                criterion,
                ("payment_type", "=", "outbound"),
                ("company_id", "in", self._ai_scope_company_ids()),
            ],
            limit=2,
        )
        if len(orders) != 1:
            raise UserError(
                _("No single outbound payment order found with the number or id %s.")
                % reference
            )
        if orders.state != "draft":
            raise UserError(
                _(
                    "The payment order %(order)s is not in draft (state: "
                    "%(state)s): bills can only be added to draft orders."
                )
                % {"order": orders.name, "state": orders.state}
            )
        return orders

    @api.model
    def _ai_acc_payorder_bill_domain(self, property_ids=None):
        domain = [
            ("move_type", "=", "in_invoice"),
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
        return domain

    @api.model
    def _ai_acc_payorder_open_lines(self, bills):
        """Open payable lines of posted vendor bills, as the wizard picks them."""
        return self.env["account.move.line"].search(
            [
                ("move_id", "in", bills.ids),
                ("move_id.state", "=", "posted"),
                ("reconciled", "=", False),
                ("credit", ">", 0),
                ("account_id.account_type", "=", "liability_payable"),
            ],
            order="date_maturity, id",
        )

    @api.model
    def _ai_acc_payorder_allowed(self, mode, order, candidates, cutoff):
        """The candidate lines the wizard of the module would offer."""
        if not order:
            order = self.env[PAYORDER_MODEL].new(
                {"payment_mode_id": mode.id, "payment_type": "outbound"}
            )
        wizard = self.env["account.payment.line.create"].new(
            {
                "order_id": order,
                "journal_ids": [(6, 0, mode.default_journal_ids.ids)],
                "target_move": "posted",
                "invoice": True,
                "allow_blocked": False,
                "date_type": "due",
                "due_date": cutoff,
                "move_date": False,
                "payment_mode": mode.default_payment_mode,
                # Every filter is given, so that no default is looked up.
                "partner_ids": [(6, 0, [])],
                "account_id": False,
                "analytic_account_id": False,
                "move_line_ids": [(6, 0, [])],
            }
        )
        domain = wizard._prepare_move_line_domain()
        return self.env["account.move.line"].search(
            domain + [("id", "in", candidates.ids)]
        )

    @api.model
    def _ai_acc_payorder_reason(self, line, mode, cutoff, busy):
        """Why the wizard leaves an open bill line out of the order."""
        if line.id in busy:
            return "already_in_order", busy[line.id]
        if line.blocked:
            return "blocked", None
        if line.date_maturity and line.date_maturity > cutoff:
            return "not_due", None
        if mode.default_journal_ids and line.journal_id not in mode.default_journal_ids:
            return "other_journal", line.journal_id.name
        if line.payment_mode_id and line.payment_mode_id != mode:
            return "other_payment_mode", line.payment_mode_id.name
        if not line.payment_mode_id and mode.default_payment_mode == "same":
            return "other_payment_mode", None
        return "excluded_by_order_filters", None

    @api.model
    def _ai_acc_payorder_journal(self, mode, order, confirm_order):
        journal = order.journal_id if order else self.env["account.journal"]
        if not order:
            if mode.bank_account_link == "fixed":
                journal = mode.fixed_journal_id
            elif len(mode.variable_journal_ids) == 1:
                journal = mode.variable_journal_ids
        if confirm_order:
            if not journal:
                raise UserError(
                    _(
                        "The order has no bank journal, so it cannot be confirmed "
                        "here: choose the journal and confirm it in Odoo."
                    )
                )
            if (
                mode.payment_method_id.bank_account_required
                and not journal.bank_account_id
            ):
                raise UserError(
                    _(
                        "The bank journal %s has no bank account: it cannot be "
                        "confirmed."
                    )
                    % journal.name
                )
        return journal

    @api.model
    def _ai_acc_payorder_bank(self, line):
        bank = line.partner_bank_id or line.partner_id.bank_ids[:1]
        number = (bank.acc_number or "").replace(" ", "")
        return bank, ("****" + number[-4:]) if number else None

    @api.model
    def _ai_acc_payorder_line_row(self, line, bank_label, reason=None, detail=None):
        row = {
            "bill_id": line.move_id.id,
            "bill": line.move_id.name,
            "supplier_ref": line.move_id.ref or None,
            "partner": line.partner_id.display_name,
            "property": line.move_id.pms_property_id.name or None,
            "due_date": line.date_maturity,
            "amount": amount(abs(line.amount_residual)),
            "bank_account": bank_label,
        }
        if reason:
            row["reason"] = reason
            if detail:
                row["detail"] = detail
        return row

    def _ai_acc_payorder_classify(self, mode, order, candidates, cutoff):
        allowed = self._ai_acc_payorder_allowed(mode, order, candidates, cutoff)
        needs_bank = mode.payment_method_id.bank_account_required
        to_add, rows, left_out = self.env["account.move.line"], [], []
        busy = {
            payment_line.move_line_id.id: payment_line.order_id.name
            for payment_line in self.env["account.payment.line"].search(
                [
                    ("move_line_id", "in", (candidates - allowed).ids),
                    ("state", "in", PAYORDER_BUSY_STATES),
                ]
            )
        }
        for line in candidates:
            bank, label = self._ai_acc_payorder_bank(line)
            if line not in allowed:
                reason, detail = self._ai_acc_payorder_reason(line, mode, cutoff, busy)
                left_out.append(
                    self._ai_acc_payorder_line_row(line, label, reason, detail)
                )
            elif needs_bank and not bank:
                left_out.append(
                    self._ai_acc_payorder_line_row(line, label, "no_bank_account")
                )
            else:
                to_add |= line
                rows.append(self._ai_acc_payorder_line_row(line, label))
        return to_add, rows, left_out

    @guarded_tool(
        input_schema={
            "payment_mode": {
                "type": "string",
                "maxLength": 100,
                "description": "New order: name of the outbound payment mode.",
            },
            "due_date": {
                "type": "string",
                "format": "date",
                "description": "New order: open vendor bills due up to this date "
                "(YYYY-MM-DD).",
            },
            "company_id": {
                "type": "integer",
                "minimum": 1,
                "description": "New order: company of the payment mode and bills.",
            },
            "property_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "maxItems": 50,
                "description": "New order: only bills of these properties.",
            },
            "partner_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "maxItems": 50,
                "description": "New order: only bills of these suppliers.",
            },
            "order": {
                "type": "string",
                "maxLength": 64,
                "description": "Existing draft payment order (number or id) to "
                "add bill_ids to.",
            },
            "bill_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "maxItems": PAYORDER_MAX_BILLS,
                "description": "Existing order: ids of the vendor bills to add.",
            },
            "confirm_order": {
                "type": "boolean",
                "description": "Also confirm the order (draft to confirmed). The "
                "bank file is never generated nor uploaded.",
            },
        },
        output_schema={
            "order": {"type": "object"},
            "lines": {"type": "array", "items": {"type": "object"}},
            "left_out": {"type": "array", "items": {"type": "object"}},
            "totals": {"type": "object"},
        },
        write=True,
        apply="_apply_acc_payment_order_prepare",
    )
    def _ai_acc_payment_order_prepare(  # noqa: C901
        self,
        payment_mode=None,
        due_date=None,
        company_id=None,
        property_ids=None,
        partner_ids=None,
        order=None,
        bill_ids=None,
        confirm_order=False,
    ):
        if PAYORDER_MODEL not in self.env:
            raise UserError(
                _(
                    "Payment orders are not available: the module "
                    "account_payment_order is not installed."
                )
            )
        if not self.env.user.has_group(PAYORDER_GROUP):
            raise AccessError(_("This tool needs access to payment orders."))
        warnings = []
        if order:
            if payment_mode or due_date or company_id or property_ids or partner_ids:
                raise UserError(
                    _(
                        "With order, give only bill_ids: payment_mode, due_date, "
                        "company_id, property_ids and partner_ids build a new order."
                    )
                )
            if not bill_ids:
                raise UserError(_("Give the bill_ids to add to the order."))
            pay_order = self._ai_acc_payorder_find_order(order)
            mode = pay_order.payment_mode_id
            cutoff = date_cls.max
            bills = self.env["account.move"].search(
                self._ai_acc_payorder_bill_domain()
                + [("id", "in", bill_ids), ("company_id", "=", pay_order.company_id.id)]
            )
            unknown = sorted(set(bill_ids) - set(bills.ids))
            if unknown:
                raise UserError(
                    _(
                        "These ids are not vendor bills of the company of the order "
                        "in your properties: %s."
                    )
                    % ", ".join(map(str, unknown))
                )
            not_posted = bills.filtered(lambda bill: bill.state != "posted")
            if not_posted:
                raise UserError(
                    _("These bills are not posted: %s.")
                    % ", ".join(not_posted.mapped(lambda b: str(b.name or b.id)))
                )
            candidates = self._ai_acc_payorder_open_lines(bills)
            paid = bills - candidates.move_id
            if paid:
                warnings.append(
                    _("Nothing is pending on these bills: %s.")
                    % ", ".join(paid.mapped("name"))
                )
        else:
            if not payment_mode or not due_date:
                raise UserError(
                    _(
                        "Give payment_mode and due_date for a new order, or order "
                        "and bill_ids to add bills to a draft order."
                    )
                )
            if bill_ids:
                raise UserError(_("bill_ids are only accepted with order."))
            pay_order = self.env[PAYORDER_MODEL]
            paid = self.env["account.move"]
            mode = self._ai_acc_payorder_find_mode(payment_mode, company_id)
            cutoff = due_date
            domain = self._ai_acc_payorder_bill_domain(property_ids) + [
                ("company_id", "=", mode.company_id.id),
                ("state", "=", "posted"),
                ("payment_state", "in", ("not_paid", "partial")),
            ]
            if partner_ids:
                partners = self.env["res.partner"].browse(partner_ids).exists()
                domain.append(
                    ("commercial_partner_id", "in", partners.commercial_partner_id.ids)
                )
            bills = self.env["account.move"].search(domain)
            candidates = self._ai_acc_payorder_open_lines(bills)
        journal = self._ai_acc_payorder_journal(mode, pay_order, confirm_order)
        to_add, rows, left_out = self._ai_acc_payorder_classify(
            mode, pay_order, candidates, cutoff
        )
        if not to_add:
            reasons = Counter(row["reason"] for row in left_out)
            raise UserError(
                _("No bill can be added to the order. Left out: %s.")
                % (", ".join(f"{k}: {v}" for k, v in sorted(reasons.items())) or "-")
            )
        if order and len(to_add.move_id) < len(bills - paid):
            warnings.append(_("Some of the given bills cannot be added: see left_out."))
        currencies = to_add.currency_id | to_add.company_currency_id
        if len(currencies) > 1:
            warnings.append(
                _(
                    "The bills are in several currencies: amounts are in the company "
                    "currency."
                )
            )
        total = sum(abs(line.amount_residual) for line in to_add)
        existing = pay_order.payment_line_ids if pay_order else []
        order_data = {
            "id": pay_order.id or None,
            "number": pay_order.name or None,
            "new": not pay_order,
            "state_after": "open" if confirm_order else "draft",
            "payment_mode": mode.name,
            "company": mode.company_id.name,
            "journal": journal.name or None,
            "existing_lines": len(existing),
            "existing_total": amount(pay_order.total_company_currency)
            if pay_order
            else 0.0,
        }
        summary = (
            _("Add %(count)s bills (%(total)s) to the payment order %(order)s.")
            % {"count": len(rows), "total": amount(total), "order": pay_order.name}
            if pay_order
            else _("Create a payment order with %(count)s bills (%(total)s).")
            % {"count": len(rows), "total": amount(total)}
        )
        if confirm_order:
            summary += " " + _("Then confirm it.")
        return {
            "summary": summary,
            "definitions": [
                _(
                    "Done as the wizard of payment orders does it, as the user: "
                    "one payment line per open payable line of each vendor bill, "
                    "for the amount still pending. Bills already in a draft, "
                    "confirmed or generated order, blocked or not due are left "
                    "out, and so are bills without a supplier bank account when "
                    "the payment method needs one."
                ),
                _(
                    "Confirming creates the payments of the order but never "
                    "generates nor uploads the bank file. bank_account shows only "
                    "the last 4 characters."
                ),
            ],
            "warnings": warnings,
            "result": {
                "order": order_data,
                "lines": rows,
                "left_out": self._ai_limit_rows(left_out, warnings),
                "totals": {
                    "lines": len(rows),
                    "bills": len(to_add.move_id),
                    "suppliers": len(to_add.partner_id),
                    "amount": amount(total),
                    "currency": mode.company_id.currency_id.name,
                    "left_out": dict(Counter(row["reason"] for row in left_out)),
                },
            },
            "plan": {
                "order_id": pay_order.id,
                "mode_id": mode.id,
                "journal_id": journal.id,
                "move_line_ids": to_add.ids,
                "confirm_order": bool(confirm_order),
            },
        }

    def _apply_acc_payment_order_prepare(self, plan, **_arguments):
        Order = self.env[PAYORDER_MODEL]
        if plan["order_id"]:
            order = Order.browse(plan["order_id"])
            if order.state != "draft":
                raise UserError(_("The order is no longer in draft; nothing was done."))
        else:
            values = {"payment_mode_id": plan["mode_id"]}
            if plan["journal_id"]:
                values["journal_id"] = plan["journal_id"]
            order = Order.create(values)
        lines = self.env["account.move.line"].browse(plan["move_line_ids"])
        wizard = (
            self.env["account.payment.line.create"]
            .with_context(active_model=PAYORDER_MODEL, active_id=order.id)
            .create(
                {
                    "order_id": order.id,
                    "date_type": "due",
                    "move_line_ids": [(6, 0, lines.ids)],
                }
            )
        )
        wizard.create_payment_lines()
        added = order.payment_line_ids.filtered(lambda pl: pl.move_line_id in lines)
        if len(added) != len(lines):
            raise UserError(
                _(
                    "The payment lines did not come out as previewed; nothing was "
                    "saved."
                )
            )
        if plan["confirm_order"]:
            order.draft2open()
        return {
            "summary": _(
                "Payment order %(order)s: %(count)s bills added, state " "%(state)s."
            )
            % {"order": order.name, "count": len(added), "state": order.state},
            "result": {
                "order": {
                    "id": order.id,
                    "number": order.name,
                    "state": order.state,
                    "payment_mode": order.payment_mode_id.name,
                    "journal": order.journal_id.name or None,
                    "lines": len(order.payment_line_ids),
                    "total": amount(order.total_company_currency),
                },
                "added_lines": len(added),
                "added_total": amount(sum(added.mapped("amount_company_currency"))),
            },
        }
