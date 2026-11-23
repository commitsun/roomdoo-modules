import re

from odoo import _, api, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import float_is_zero

from odoo.addons.pms_ai_tool.tools import guarded_tool, truncate

# Longest numeric id accepted as a folio id; anything longer is a folio code.
MAX_FOLIO_ID_DIGITS = 12
MAX_INVOICE_LINES = 50


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    @api.model
    def _ai_billing_find_folio(self, reference):
        """The folio with that id or code, among the ones the user can reach."""
        reference = reference.strip()
        if re.fullmatch(rf"\d{{1,{MAX_FOLIO_ID_DIGITS}}}", reference):
            criterion = ("id", "=", int(reference))
        else:
            criterion = ("name", "=", reference)
        folios = self.env["pms.folio"].search(
            [("pms_property_id", "in", self._ai_scope_property_ids()), criterion],
            limit=2,
        )
        if not folios:
            raise UserError(_("No folio found with the id or code %s.") % reference)
        if len(folios) > 1:
            raise UserError(
                _("Several folios have the code %s: use the id of the folio.")
                % reference
            )
        return folios

    @api.model
    def _ai_billing_partner(self, folio, partner_id):
        """The customer to invoice: the one asked for or the wizard default."""
        if not partner_id:
            # Default of the invoicing wizard of the folio: its first billing
            # contact, or none (each line goes to its own invoice contact).
            return folio.partner_invoice_ids[:1]
        partner = self.env["res.partner"].browse(partner_id).exists()
        try:
            partner.check_access_rights("read")
            partner.check_access_rule("read")
        except AccessError:
            partner = self.env["res.partner"]
        if not partner:
            raise UserError(_("No customer found with the id %s.") % partner_id)
        return partner

    @api.model
    def _ai_billing_lines_to_invoice(self, folio, sale_line_ids):
        """Sale lines and quantities, as the folio wizard passes them on."""
        wizard = self.env["folio.advance.payment.inv"]
        if not sale_line_ids:
            # The wizard with its defaults: rooms and services of the folio.
            return wizard._get_lines_to_invoice(folios=folio)
        foreign = sorted(set(sale_line_ids) - set(folio.sale_line_ids.ids))
        if foreign:
            raise UserError(
                _("The lines %(lines)s are not lines of the folio %(folio)s.")
                % {"lines": ", ".join(map(str, foreign)), "folio": folio.name}
            )
        lines = self.env["folio.sale.line"].browse(sale_line_ids)
        precision = self.env["decimal.precision"].precision_get(
            "Product Unit of Measure"
        )
        empty = lines.filtered(
            lambda line: not line.display_type
            and float_is_zero(line.qty_to_invoice, precision_digits=precision)
        )
        if empty:
            raise UserError(
                _("The lines %s have nothing to invoice.")
                % ", ".join(map(str, sorted(empty.ids)))
            )
        # Keep the heading of each reservation (it names the room), as the
        # invoice of the whole folio does, when it goes to the same contact.
        for line in lines:
            section = line.section_id
            if section and section.default_invoice_to == line.default_invoice_to:
                lines |= section
        return {
            line.id: 0 if line.display_type else line.qty_to_invoice for line in lines
        }

    @api.model
    def _ai_billing_invoice_vals(self, folio, lines_to_invoice, partner):
        """Values of the invoices that ``_create_invoices`` would create."""
        vals_list = folio.get_invoice_vals_list(
            final=True,
            lines_to_invoice=lines_to_invoice,
            partner_invoice_id=partner.id or False,
        )
        return folio._get_group_vals_list(vals_list) if vals_list else []

    @api.model
    def _ai_billing_invoice_preview(self, folio, vals):
        partner = self.env["res.partner"].browse(vals["partner_id"])
        journal = self.env["account.journal"].browse(vals["journal_id"])
        currency = self.env["res.currency"].browse(vals["currency_id"])
        lines = []
        untaxed = total = 0.0
        for command in vals["invoice_line_ids"]:
            line_vals = command[2]
            if line_vals.get("display_type"):
                continue
            taxes = self.env["account.tax"].browse(line_vals["tax_ids"][0][2])
            discount = line_vals.get("discount") or 0.0
            amounts = taxes.compute_all(
                line_vals["price_unit"] * (1 - discount / 100.0),
                currency=currency,
                quantity=line_vals["quantity"],
                product=self.env["product.product"].browse(line_vals["product_id"]),
                partner=partner,
            )
            untaxed += amounts["total_excluded"]
            total += amounts["total_included"]
            lines.append(
                {
                    "sale_line_id": line_vals["folio_line_ids"][0][2][0],
                    "description": truncate(line_vals["name"]),
                    "quantity": line_vals["quantity"],
                    "price_unit": line_vals["price_unit"],
                    "discount": discount,
                    "taxes": ", ".join(taxes.mapped("name")),
                    "amount_untaxed": currency.round(amounts["total_excluded"]),
                    "amount_total": currency.round(amounts["total_included"]),
                }
            )
        return {
            "partner_id": partner.id,
            "partner": partner.display_name,
            "partner_vat": partner.vat or None,
            "journal_id": journal.id,
            "journal": journal.name,
            "invoice_type": "simplified" if journal.is_simplified_invoice else "full",
            "document": "credit_note" if total < 0 else "invoice",
            "lines": lines,
            "amount_untaxed": currency.round(untaxed),
            "amount_tax": currency.round(total - untaxed),
            "amount_total": currency.round(total),
            "currency": currency.name,
        }

    @api.model
    def _ai_billing_invoice_warnings(self, folio, invoice, post):
        """Data problems of an invoice; the ones that stop posting refuse it."""
        partner = self.env["res.partner"].browse(invoice["partner_id"])
        warnings, blocking = [], []
        if invoice["invoice_type"] == "full":
            if (
                folio.company_id.check_min_partner_data_invoice
                and not partner._check_enought_invoice_data()
            ):
                blocking.append(
                    _(
                        "%s lacks the tax id or the address that a full invoice "
                        "needs: the invoice can be created as a draft, but it "
                        "cannot be posted until the customer data is completed."
                    )
                    % partner.display_name
                )
            elif not partner.vat:
                warnings.append(_("%s has no VAT number.") % partner.display_name)
        elif (
            invoice["amount_total"]
            > folio.pms_property_id.max_amount_simplified_invoice
        ):
            blocking.append(
                _(
                    "The simplified invoice exceeds the maximum of %s for simplified "
                    "invoices of the property: it cannot be posted; invoice an "
                    "identified customer instead."
                )
                % folio.pms_property_id.max_amount_simplified_invoice
            )
        if post and blocking:
            raise UserError(
                " ".join(blocking)
                + " "
                + _("Leave the invoice in draft (post=false) or fix it first.")
            )
        return warnings + blocking

    @guarded_tool(
        input_schema={
            "folio": {
                "type": "string",
                "maxLength": 40,
                "description": "Folio code (for example 123/26/000456) or its "
                "numeric id.",
            },
            "sale_line_ids": {
                "type": "array",
                "maxItems": MAX_INVOICE_LINES,
                "items": {"type": "integer"},
                "description": "Invoice only these sale lines of the folio (the "
                "sale_line_id values of a previous preview). Default: everything "
                "pending of the folio, as its Create invoice button does.",
            },
            "partner_id": {
                "type": "integer",
                "description": "Id of the customer to invoice instead of the "
                "billing contact of the folio. The anonymous customer gets a "
                "simplified invoice.",
            },
            "post": {
                "type": "boolean",
                "description": "True posts the invoices; false (default) leaves "
                "them in draft.",
            },
        },
        output_schema={
            "folio": {"type": "object"},
            "invoices": {"type": "array"},
            "post": {"type": "boolean"},
        },
        required_inputs=["folio"],
        write=True,
        apply="_apply_pms_folio_invoice",
        log_result=False,
    )
    def _ai_pms_folio_invoice(
        self, folio, sale_line_ids=None, partner_id=None, post=False
    ):
        folio = self._ai_billing_find_folio(folio)
        partner = self._ai_billing_partner(folio, partner_id)
        lines_to_invoice = self._ai_billing_lines_to_invoice(folio, sale_line_ids)
        vals_list = self._ai_billing_invoice_vals(folio, lines_to_invoice, partner)
        invoices = [self._ai_billing_invoice_preview(folio, v) for v in vals_list]
        if not any(invoice["lines"] for invoice in invoices):
            raise UserError(_("The folio %s has nothing to invoice.") % folio.name)
        empty = [invoice["partner"] for invoice in invoices if not invoice["lines"]]
        if empty:
            raise UserError(
                _(
                    "The invoice for %s would have no lines: choose the lines or "
                    "the customer to invoice."
                )
                % ", ".join(empty)
            )
        warnings = []
        for invoice in invoices:
            warnings += self._ai_billing_invoice_warnings(folio, invoice, post)
        total = sum(invoice["amount_total"] for invoice in invoices)
        summary = (
            _(
                "Create and post %(count)s invoice(s) of the folio %(folio)s for "
                "a total of %(total)s."
            )
            if post
            else _(
                "Create %(count)s draft invoice(s) of the folio %(folio)s for a "
                "total of %(total)s."
            )
        )
        return {
            "summary": summary
            % {
                "count": len(invoices),
                "folio": folio.name,
                "total": folio.currency_id.round(total),
            },
            "definitions": [
                _(
                    "The invoices are created by the invoicing of the folio itself "
                    "(its Create invoice button): its lines, taxes, journal, "
                    "fiscal position and down payment deductions. The anonymous "
                    "customer gets a simplified invoice; any other customer, a "
                    "full invoice. A negative total becomes a credit note."
                ),
                _(
                    "Amounts are computed from the taxes of each line; the invoice "
                    "keeps the rounding of the accounting."
                ),
            ],
            "warnings": warnings,
            "result": {
                "folio": {
                    "id": folio.id,
                    "code": folio.name,
                    "property": folio.pms_property_id.name,
                },
                "invoices": invoices,
                "post": bool(post),
            },
            "plan": {
                "folio_id": folio.id,
                "lines_to_invoice": sorted(lines_to_invoice.items()),
                "partner_invoice_id": partner.id or False,
                "post": bool(post),
                "invoices": invoices,
            },
        }

    def _apply_pms_folio_invoice(self, plan, folio, **arguments):
        folio = self.env["pms.folio"].browse(plan["folio_id"])
        # The same call as the invoicing wizard of the folio, with its
        # default of deducting the down payments.
        moves = folio._create_invoices(
            final=True,
            lines_to_invoice=dict(plan["lines_to_invoice"]),
            partner_invoice_id=plan["partner_invoice_id"],
        )
        if not moves:
            raise UserError(_("No invoice was created: you cannot create invoices."))
        moves = self.env["account.move"].browse(moves.ids)
        self._ai_billing_check_invoices(moves, plan["invoices"])
        if plan["post"]:
            moves.action_post()
        return {
            "summary": _("%(count)s invoice(s) of the folio %(folio)s created.")
            % {"count": len(moves), "folio": folio.name},
            "result": {
                "folio_id": folio.id,
                "folio": folio.name,
                "invoices": [
                    {
                        "id": move.id,
                        "number": move.name,
                        "state": move.state,
                        "move_type": move.move_type,
                        "partner_id": move.partner_id.id,
                        "partner": move.partner_id.display_name,
                        "journal": move.journal_id.name,
                        "invoice_type": "simplified"
                        if move.journal_id.is_simplified_invoice
                        else "full",
                        "amount_untaxed": move.amount_untaxed,
                        "amount_tax": move.amount_tax,
                        "amount_total": move.amount_total,
                    }
                    for move in moves
                ],
            },
        }

    @api.model
    def _ai_billing_check_invoices(self, moves, expected):
        """Stop (and roll back) if the invoices differ from the preview."""

        def key(partner_id, journal_id, sale_line_ids):
            return partner_id, journal_id, tuple(sorted(sale_line_ids))

        created = sorted(
            key(
                move.partner_id.id,
                move.journal_id.id,
                move.invoice_line_ids.filtered(
                    lambda line: line.display_type == "product"
                ).folio_line_ids.ids,
            )
            for move in moves
        )
        previewed = sorted(
            key(
                invoice["partner_id"],
                invoice["journal_id"],
                [line["sale_line_id"] for line in invoice["lines"]],
            )
            for invoice in expected
        )
        totals = sorted(abs(move.amount_total) for move in moves)
        expected_totals = sorted(abs(invoice["amount_total"]) for invoice in expected)
        tolerance = 0.01 * max(len(moves.invoice_line_ids), 1)
        if created != previewed or any(
            abs(a - b) > tolerance for a, b in zip(totals, expected_totals, strict=True)
        ):
            raise UserError(
                _(
                    "The invoices do not match the preview; nothing was saved. "
                    "Preview the change again."
                )
            )
