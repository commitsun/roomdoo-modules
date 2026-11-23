from odoo import _, fields, models
from odoo.exceptions import UserError

from odoo.addons.base.models.res_bank import sanitize_account_number
from odoo.addons.pms_ai_tool.tools import guarded_tool

from .acc_vendor_bill_common import MAX_MATCHES, like_escape
from .common import amount

MAX_LINES = 20
MAX_TAXES = 5
# Rounding allowed between the totals of the preview and the saved ones.
TOTAL_TOLERANCE = 0.011

HEADER_FIELDS = (
    "partner_id",
    "ref",
    "payment_reference",
    "invoice_date",
    "date",
    "invoice_date_due",
    "invoice_payment_term_id",
    "partner_bank_id",
    "payment_mode_id",
    "fiscal_position_id",
    "to_check",
)
LINE_FIELDS = ("account_id", "tax_ids", "name", "quantity", "price_unit")


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    # Snapshots: plain values, compared before and after the change.

    def _ai_vb_header_fields(self):
        move_fields = self.env["account.move"]._fields
        return [name for name in HEADER_FIELDS if name in move_fields]

    def _ai_vb_header(self, move):
        values = {}
        for name in self._ai_vb_header_fields():
            value = move[name]
            field = move._fields[name]
            if field.type == "many2one":
                value = value.id or None
            elif field.type == "date":
                value = fields.Date.to_string(value) if value else None
            elif field.type == "boolean":
                value = bool(value)
            else:
                value = value or None
            values[name] = value
        return values

    def _ai_vb_line(self, line):
        return {
            "account_id": line.account_id.id or None,
            "tax_ids": sorted(line.tax_ids.ids),
            "name": line.name or None,
            "quantity": line.quantity,
            "price_unit": line.price_unit,
        }

    def _ai_vb_show(self, model, name, value):
        """Readable value of a snapshot entry."""
        field = self.env[model]._fields[name]
        if value in (None, False, []):
            return False if field.type == "boolean" else None
        if field.type == "many2one":
            return self.env[field.comodel_name].browse(value).display_name
        if field.type == "many2many":
            return self.env[field.comodel_name].browse(value).mapped("name")
        return value

    def _ai_vb_label(self, move):
        return move.name if move.name and move.name != "/" else move.id

    # Lookups

    def _ai_vb_edit_bill(self, bill_id, bill):
        if bool(bill_id) == bool(bill):
            raise UserError(
                _(
                    "Give the bill to edit: bill_id, or bill with its number or "
                    "supplier reference."
                )
            )
        Move = self.env["account.move"]
        if bill_id:
            moves = Move.search(self._ai_vb_domain() + [("id", "=", bill_id)])
        else:
            moves = self._ai_vb_find(bill)
        if not moves:
            raise UserError(
                _(
                    "No vendor bill or vendor credit note %s was found within your "
                    "companies and properties."
                )
                % (bill_id or bill)
            )
        if len(moves) > 1:
            raise self._ai_vb_ambiguous(bill, moves)
        if moves.state != "draft":
            raise UserError(
                _(
                    "The bill %(bill)s is not in draft (state: %(state)s); only "
                    "draft bills can be edited."
                )
                % {"bill": moves.name or moves.id, "state": moves.state}
            )
        moves.check_access_rights("write")
        moves.check_access_rule("write")
        return moves

    def _ai_vb_partner(self, move, partner_id, partner_vat):
        if partner_id and partner_vat:
            raise UserError(_("Give partner_id or partner_vat, not both."))
        Partner = self.env["res.partner"]
        company_domain = [("company_id", "in", [False, move.company_id.id])]
        if partner_id:
            partners = Partner.search(company_domain + [("id", "=", partner_id)])
            label = partner_id
        else:
            vat = "".join(partner_vat.split()).upper()
            partners = Partner.search(
                company_domain
                + [
                    ("parent_id", "=", False),
                    "|",
                    ("vat", "=ilike", like_escape(vat)),
                    ("vat", "=ilike", "__" + like_escape(vat)),
                ],
                limit=MAX_MATCHES + 1,
            )
            label = vat
        if not partners:
            raise UserError(
                _("No active partner %s was found for the company of the bill.") % label
            )
        if len(partners) > 1:
            raise UserError(
                _("Several partners match %(vat)s: %(matches)s. Use partner_id.")
                % {
                    "vat": label,
                    "matches": ", ".join(
                        f"{partner.id} ({partner.display_name})"
                        for partner in partners[:MAX_MATCHES]
                    ),
                }
            )
        return partners

    def _ai_vb_bank_partner(self, move, partner):
        """Owner of the bank account of the bill, as Odoo computes it."""
        if move.is_inbound():
            return move.company_id.partner_id
        return partner.commercial_partner_id

    def _ai_vb_banks(self, move, bank_partner):
        return bank_partner.bank_ids.filtered(
            lambda bank: not bank.company_id or bank.company_id == move.company_id
        )

    def _ai_vb_bank(self, move, bank_partner, acc_number):
        banks = self._ai_vb_banks(move, bank_partner)
        wanted = sanitize_account_number(acc_number)
        found = banks.filtered(lambda bank: bank.sanitized_acc_number == wanted)
        if len(found) != 1:
            raise UserError(
                _(
                    "The bank account %(number)s is not one of the accounts of "
                    "%(partner)s. Their accounts: %(accounts)s."
                )
                % {
                    "number": acc_number,
                    "partner": bank_partner.display_name,
                    "accounts": ", ".join(banks.mapped("acc_number")) or "-",
                }
            )
        return found

    def _ai_vb_payment_mode(self, move, name):
        if "payment_mode_id" not in move._fields:
            raise UserError(
                _(
                    "Payment modes are not installed in this database (module "
                    "account_payment_partner): payment_mode cannot be changed."
                )
            )
        Mode = self.env["account.payment.mode"]
        domain = [("company_id", "=", move.company_id.id)]
        modes = Mode.search(domain + [("name", "=ilike", like_escape(name))])
        if not modes:
            modes = Mode.search(domain + [("name", "ilike", name)], limit=MAX_MATCHES)
        if len(modes) != 1:
            raise UserError(
                _("The payment mode %(name)s must match exactly one: %(matches)s.")
                % {"name": name, "matches": ", ".join(modes.mapped("name")) or "-"}
            )
        payment_type = "outbound" if move.move_type == "in_invoice" else "inbound"
        if modes.payment_type != payment_type:
            raise UserError(
                _("The payment mode %(name)s is not for %(type)s payments.")
                % {"name": modes.name, "type": payment_type}
            )
        return modes

    def _ai_vb_account(self, move, code):
        accounts = self.env["account.account"].search(
            [
                ("company_id", "=", move.company_id.id),
                ("code", "=", code.strip()),
                ("deprecated", "=", False),
            ]
        )
        if len(accounts) != 1:
            raise UserError(
                _("No active account with code %s exists in the company of the bill.")
                % code
            )
        if accounts.account_type in (
            "asset_receivable",
            "liability_payable",
            "off_balance",
        ):
            raise UserError(
                _("The account %s cannot be used on the lines of a vendor bill.") % code
            )
        return accounts

    def _ai_vb_taxes(self, move, names):
        Tax = self.env["account.tax"]
        taxes = Tax
        domain = [
            ("company_id", "=", move.company_id.id),
            ("type_tax_use", "=", "purchase"),
        ]
        for name in names:
            found = Tax.search(domain + [("name", "=ilike", like_escape(name))])
            if not found:
                found = Tax.search(
                    domain + [("name", "ilike", name)], limit=MAX_MATCHES
                )
            if len(found) != 1:
                raise UserError(
                    _(
                        "The purchase tax %(name)s must match exactly one tax of the "
                        "company of the bill: %(matches)s."
                    )
                    % {
                        "name": name,
                        "matches": ", ".join(found.mapped("name")) or "-",
                    }
                )
            taxes |= found
        return taxes

    def _ai_vb_lines(self, move, line_ids):
        product_lines = move.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        if not line_ids:
            raise UserError(
                _(
                    "Give the lines to change (lines). The lines of this bill are: "
                    "%s."
                )
                % ", ".join(
                    f"{line.id} ({line.name or '-'}, {line.account_id.code or '-'}, "
                    f"{amount(line.price_subtotal)})"
                    for line in product_lines
                )
            )
        unknown = set(line_ids) - set(product_lines.ids)
        if unknown:
            raise UserError(
                _("The lines %(lines)s are not product lines of the bill %(bill)s.")
                % {
                    "lines": ", ".join(map(str, sorted(unknown))),
                    "bill": move.name or move.id,
                }
            )
        return product_lines.filtered(lambda line: line.id in line_ids)

    # Totals without writing

    def _ai_vb_totals(self, move, line_values, partner):
        """Untaxed amount and total of the product lines with the new values."""
        untaxed = total = 0.0
        for line in move.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        ):
            values = line_values.get(line.id) or self._ai_vb_line(line)
            taxes = self.env["account.tax"].browse(values["tax_ids"])
            price = values["price_unit"] * (1 - (line.discount or 0.0) / 100.0)
            computed = taxes.compute_all(
                price,
                currency=move.currency_id,
                quantity=values["quantity"],
                product=line.product_id,
                partner=partner,
                is_refund=move.move_type == "in_refund",
            )
            untaxed += computed["total_excluded"]
            total += computed["total_included"]
        return untaxed, total

    # Changes

    def _ai_vb_new_header(self, move, header, changes):  # noqa: C901
        """Header values after the change, with what Odoo derives from them."""
        new = dict(header)
        warnings = []
        partner = changes.get("partner")
        if partner and partner.id != header["partner_id"]:
            new["partner_id"] = partner.id
            # As Odoo does when the partner changes: its supplier payment term
            # and payment mode, and one of its bank accounts.
            term = partner.with_company(
                move.company_id
            ).property_supplier_payment_term_id
            new["invoice_payment_term_id"] = term.id or None
            if "payment_mode_id" in new and move.move_type == "in_invoice":
                mode = partner.with_company(move.company_id).supplier_payment_mode_id
                if mode:
                    new["payment_mode_id"] = mode.id
            bank_partner = self._ai_vb_bank_partner(move, partner)
            if move.partner_bank_id.partner_id != bank_partner:
                banks = self._ai_vb_banks(move, bank_partner).filtered(
                    "allow_out_payment"
                )
                new["partner_bank_id"] = banks[:1].id or None
            position = (
                self.env["account.fiscal.position"]
                .with_company(move.company_id)
                ._get_fiscal_position(partner)
            )
            if position.id != (header["fiscal_position_id"] or False):
                warnings.append(
                    _(
                        "The new partner would normally use the fiscal position "
                        "%(new)s; the bill keeps %(old)s and its taxes are not "
                        "remapped: review them."
                    )
                    % {
                        "new": position.name or "-",
                        "old": move.fiscal_position_id.name or "-",
                    }
                )
        for name in ("ref", "payment_reference"):
            if name in changes:
                new[name] = changes[name] or None
        if "to_check" in changes:
            new["to_check"] = changes["to_check"]
        if "payment_mode" in changes:
            new["payment_mode_id"] = changes["payment_mode"].id
        if "partner_bank" in changes:
            new["partner_bank_id"] = changes["partner_bank"].id
        invoice_date = changes.get("invoice_date")
        if invoice_date:
            new["invoice_date"] = fields.Date.to_string(invoice_date)
            if "date" not in changes and new["invoice_date"] != header["invoice_date"]:
                # Odoo derives the accounting date from the invoice date.
                new["date"] = fields.Date.to_string(
                    move._get_accounting_date(invoice_date, move._affect_tax_report())
                )
        if changes.get("date"):
            new["date"] = fields.Date.to_string(changes["date"])
        return new, warnings

    def _ai_vb_due_date(self, move, header, new, changes, totals_change):
        """The due date after the change; Odoo derives it from the payment term."""
        term = self.env["account.payment.term"].browse(new["invoice_payment_term_id"])
        if changes.get("invoice_date_due"):
            if term:
                raise UserError(
                    _(
                        "The bill has the payment term %s: its due date is computed "
                        "from it and cannot be set."
                    )
                    % term.name
                )
            return fields.Date.to_string(changes["invoice_date_due"])
        derived = (
            new["invoice_date"] != header["invoice_date"]
            or new["invoice_payment_term_id"] != header["invoice_payment_term_id"]
            or totals_change
        )
        if term.line_ids and derived:
            date_ref = fields.Date.to_date(
                new["invoice_date"] or new["date"]
            ) or fields.Date.context_today(self)
            return fields.Date.to_string(
                max(line._get_due_date(date_ref) for line in term.line_ids)
            )
        return header["invoice_date_due"]

    def _ai_vb_line_values(self, move, changes):
        lines = changes.get("lines")
        if not lines:
            return {}, []
        line_values, rows = {}, []
        for line in lines:
            old = self._ai_vb_line(line)
            new = dict(old)
            if "account" in changes:
                new["account_id"] = changes["account"].id
            if "taxes" in changes:
                new["tax_ids"] = sorted(changes["taxes"].ids)
            if changes.get("label"):
                new["name"] = changes["label"]
            if "quantity" in changes:
                new["quantity"] = changes["quantity"]
            if "price_unit" in changes:
                new["price_unit"] = changes["price_unit"]
            line_changes = [
                {
                    "field": name,
                    "old": self._ai_vb_show("account.move.line", name, old[name]),
                    "new": self._ai_vb_show("account.move.line", name, new[name]),
                }
                for name in LINE_FIELDS
                if old[name] != new[name]
            ]
            if line_changes:
                line_values[line.id] = new
                rows.append({"line_id": line.id, "changes": line_changes})
        return line_values, rows

    def _ai_vb_edit_changes(self, move, args):
        """Resolve the arguments into records and values."""
        changes = {}
        if args.get("partner_id") or args.get("partner_vat"):
            changes["partner"] = self._ai_vb_partner(
                move, args.get("partner_id"), args.get("partner_vat")
            )
        for name in (
            "ref",
            "payment_reference",
            "invoice_date",
            "date",
            "invoice_date_due",
            "to_check",
            "label",
        ):
            if args.get(name) is not None:
                changes[name] = args[name]
        if args.get("payment_mode"):
            changes["payment_mode"] = self._ai_vb_payment_mode(
                move, args["payment_mode"]
            )
        if args.get("partner_bank"):
            partner = changes.get("partner") or move.partner_id
            changes["partner_bank"] = self._ai_vb_bank(
                move, self._ai_vb_bank_partner(move, partner), args["partner_bank"]
            )
        if args.get("account"):
            changes["account"] = self._ai_vb_account(move, args["account"])
        if args.get("taxes"):
            changes["taxes"] = self._ai_vb_taxes(move, args["taxes"])
        for name in ("quantity", "price_unit"):
            if args.get(name) is not None:
                changes[name] = args[name]
        line_args = {"account", "taxes", "label", "quantity", "price_unit"}
        if line_args & set(changes) or args.get("lines"):
            if not line_args & set(changes):
                raise UserError(
                    _(
                        "Give the change for the lines: account, taxes, label, "
                        "quantity or price_unit."
                    )
                )
            changes["lines"] = self._ai_vb_lines(move, args.get("lines"))
            single = {"quantity", "price_unit"} & set(changes)
            if single and len(changes["lines"]) != 1:
                raise UserError(
                    _("quantity and price_unit can only be changed on a single line.")
                )
        return changes

    @guarded_tool(
        input_schema={
            "bill_id": {
                "type": "integer",
                "minimum": 1,
                "description": "Id of the vendor bill or vendor credit note.",
            },
            "bill": {
                "type": "string",
                "maxLength": 64,
                "description": "Number or supplier reference of the bill, when "
                "bill_id is not known. It must match exactly one bill.",
            },
            "partner_id": {
                "type": "integer",
                "minimum": 1,
                "description": "New partner (res.partner id).",
            },
            "partner_vat": {
                "type": "string",
                "maxLength": 32,
                "description": "New partner, by VAT number (with or without "
                "country prefix). It must match exactly one partner.",
            },
            "ref": {
                "type": "string",
                "maxLength": 128,
                "description": "New supplier reference (the supplier's invoice "
                "number).",
            },
            "payment_reference": {
                "type": "string",
                "maxLength": 128,
                "description": "New payment reference.",
            },
            "invoice_date": {
                "type": "string",
                "format": "date",
                "description": "New invoice date (YYYY-MM-DD). Unless date is "
                "given, Odoo derives the accounting date from it.",
            },
            "date": {
                "type": "string",
                "format": "date",
                "description": "New accounting date (YYYY-MM-DD).",
            },
            "invoice_date_due": {
                "type": "string",
                "format": "date",
                "description": "New due date (YYYY-MM-DD). Refused when the "
                "bill has a payment term, which computes it.",
            },
            "partner_bank": {
                "type": "string",
                "maxLength": 64,
                "description": "Bank account number (IBAN) to pay; it must be "
                "one of the partner's bank accounts.",
            },
            "payment_mode": {
                "type": "string",
                "maxLength": 64,
                "description": "Name of the new payment mode (only where "
                "payment modes are installed).",
            },
            "to_check": {
                "type": "boolean",
                "description": "Set or clear the 'to check' flag.",
            },
            "lines": {
                "type": "array",
                "items": {"type": "integer"},
                "maxItems": MAX_LINES,
                "description": "Ids of the bill's product lines that get the "
                "line changes (account, taxes, label, quantity, price_unit).",
            },
            "account": {
                "type": "string",
                "maxLength": 32,
                "description": "New account code for all the given lines.",
            },
            "taxes": {
                "type": "array",
                "items": {"type": "string", "maxLength": 64},
                "maxItems": MAX_TAXES,
                "description": "Names of the purchase taxes that replace the "
                "taxes of all the given lines.",
            },
            "label": {
                "type": "string",
                "maxLength": 256,
                "description": "New label for all the given lines.",
            },
            "quantity": {
                "type": "number",
                "description": "New quantity; only with exactly one line.",
            },
            "price_unit": {
                "type": "number",
                "description": "New unit price; only with exactly one line.",
            },
        },
        output_schema={
            "bill": {"type": "object"},
            "header_changes": {"type": "array", "items": {"type": "object"}},
            "line_changes": {"type": "array", "items": {"type": "object"}},
            "totals_before": {"type": "object"},
            "totals_after": {"type": "object"},
        },
        write=True,
        apply="_ai_apply_acc_vendor_bill_edit",
        log_result=False,
    )
    def _ai_acc_vendor_bill_edit(self, bill_id=None, bill=None, **args):
        move = self._ai_vb_edit_bill(bill_id, bill)
        changes = self._ai_vb_edit_changes(move, args)
        header = self._ai_vb_header(move)
        new_header, warnings = self._ai_vb_new_header(move, header, changes)
        line_values, line_rows = self._ai_vb_line_values(move, changes)
        partner = self.env["res.partner"].browse(new_header["partner_id"])
        # Odoo's own totals may round differently from compute_all: apply only
        # the difference the change makes.
        old_untaxed, old_total = self._ai_vb_totals(move, {}, move.partner_id)
        new_untaxed, new_total = self._ai_vb_totals(move, line_values, partner)
        untaxed = move.amount_untaxed + new_untaxed - old_untaxed
        total = move.amount_total + new_total - old_total
        totals_change = (
            abs(untaxed - move.amount_untaxed) > 0.001
            or abs(total - move.amount_total) > 0.001
        )
        new_header["invoice_date_due"] = self._ai_vb_due_date(
            move, header, new_header, changes, totals_change
        )
        header_rows = [
            {
                "field": name,
                "old": self._ai_vb_show("account.move", name, header[name]),
                "new": self._ai_vb_show("account.move", name, new_header[name]),
            }
            for name in header
            if header[name] != new_header[name]
        ]
        if not header_rows and not line_rows:
            raise UserError(_("Nothing to change: the bill already has these values."))
        if new_header["partner_bank_id"]:
            bank = self.env["res.partner.bank"].browse(new_header["partner_bank_id"])
            if not bank.allow_out_payment:
                warnings.append(
                    _("The bank account %s is not trusted for outgoing payments.")
                    % bank.acc_number
                )
        if total <= 0:
            warnings.append(_("After the change the total is not positive."))
        before = {
            "untaxed": amount(move.amount_untaxed),
            "tax": amount(move.amount_tax),
            "total": amount(move.amount_total),
        }
        after = {
            "untaxed": amount(untaxed),
            "tax": amount(total - untaxed),
            "total": amount(total),
        }
        result = {
            "bill": self._ai_vb_describe(move),
            "header_changes": header_rows,
            "line_changes": line_rows,
            "totals_before": before,
            "totals_after": after,
        }
        stamps = [str(move.write_date)] + [
            str(line.write_date) for line in move.line_ids.sorted("id")
        ]
        return {
            "summary": _(
                "Bill %(bill)s: %(header)s header changes and %(lines)s changed "
                "lines; total %(old)s -> %(new)s."
            )
            % {
                "bill": self._ai_vb_label(move),
                "header": len(header_rows),
                "lines": len(line_rows),
                "old": before["total"],
                "new": after["total"],
            },
            "definitions": [
                _(
                    "Only draft vendor bills and vendor credit notes of your "
                    "companies and properties can be edited. The change is saved "
                    "with your user, so Odoo's access rules and lock dates apply."
                ),
                _(
                    "When the partner changes, the bill takes the partner's supplier "
                    "payment term, payment mode and first trusted bank account, as "
                    "in Odoo; the due date follows the payment term and the "
                    "accounting date follows the invoice date."
                ),
                _(
                    "totals_after is computed from the new line values without "
                    "saving; if the saved result differs from this preview, nothing "
                    "is saved."
                ),
            ],
            "warnings": warnings,
            "result": result,
            "binding": {"result": result, "stamps": stamps},
            "plan": {
                "move_id": move.id,
                "header": new_header,
                "lines": line_values,
                "totals": after,
            },
        }

    def _ai_vb_write_values(self, move, plan):
        """Only the changed values, plus what Odoo would otherwise recompute."""
        header = self._ai_vb_header(move)
        changed = {name for name, new in plan["header"].items() if new != header[name]}
        keep = set()
        if "partner_id" in changed:
            keep |= {
                "invoice_payment_term_id",
                "partner_bank_id",
                "payment_mode_id",
                "fiscal_position_id",
            }
        if "invoice_date" in changed:
            keep.add("date")
        if "payment_mode_id" in changed:
            keep.add("partner_bank_id")
        names = (changed | keep) & set(plan["header"])
        if plan["header"]["invoice_payment_term_id"]:
            # The due date comes from the payment term.
            names.discard("invoice_date_due")
        values = {name: plan["header"][name] or False for name in names}
        line_commands = []
        for line_id, expected in plan["lines"].items():
            line = self.env["account.move.line"].browse(line_id)
            current = self._ai_vb_line(line)
            line_values = {
                name: expected[name]
                for name in LINE_FIELDS
                if expected[name] != current[name]
            }
            if "tax_ids" in line_values:
                line_values["tax_ids"] = [(6, 0, line_values["tax_ids"])]
            if line_values:
                line_commands.append((1, line_id, line_values))
        if line_commands:
            values["invoice_line_ids"] = line_commands
        return values

    def _ai_apply_acc_vendor_bill_edit(self, plan, **arguments):
        move = self.env["account.move"].browse(plan["move_id"])
        if move.state != "draft":
            raise UserError(_("The bill is no longer in draft; nothing was changed."))
        old_total = amount(move.amount_total)
        move.write(self._ai_vb_write_values(move, plan))
        self.env.flush_all()
        move.invalidate_recordset()
        move.line_ids.invalidate_recordset()
        differences = [
            name
            for name, value in self._ai_vb_header(move).items()
            if value != plan["header"][name]
        ]
        for line_id, expected in plan["lines"].items():
            line = self.env["account.move.line"].browse(line_id)
            differences += [
                f"{line_id}.{name}"
                for name, value in self._ai_vb_line(line).items()
                if value != expected[name]
            ]
        totals = {
            "untaxed": amount(move.amount_untaxed),
            "tax": amount(move.amount_tax),
            "total": amount(move.amount_total),
        }
        for name in ("untaxed", "total"):
            if abs(totals[name] - plan["totals"][name]) > TOTAL_TOLERANCE:
                differences.append(name)
        if differences:
            raise UserError(
                _(
                    "Nothing was saved: once applied, the bill would differ from "
                    "the preview in %s. Review it in Odoo."
                )
                % ", ".join(differences)
            )
        return {
            "summary": _("Bill %(bill)s changed; total %(old)s -> %(new)s.")
            % {
                "bill": self._ai_vb_label(move),
                "old": old_total,
                "new": totals["total"],
            },
            "result": {"bill": self._ai_vb_describe(move), "totals": totals},
        }
