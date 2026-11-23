import re

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.pms_ai_tool.tools import guarded_tool

from .common import amount

REVERSE_INVOICE_TYPES = ("out_invoice", "in_invoice")
REVERSE_MODES = ("refund", "cancel", "modify")
# Longest numeric id accepted as an invoice id; anything else is a number.
REVERSE_MAX_ID_DIGITS = 12
# Modules that may report a posted credit note to the tax authority.
REVERSE_FISCAL_MODULES = (
    "l10n_es_aeat_sii_oca",
    "l10n_es_verifactu_oca",
    "l10n_es_ticketbai",
    "l10n_es_ticketbai_batuz",
    "l10n_es_facturae",
    "l10n_es_edi_sii",
    "l10n_es_edi_tbai",
    "l10n_es_edi_verifactu",
)
# Fields of the standard reversal wizard. Any other field comes from a
# localization module and is reported in the preview with its value.
REVERSE_STANDARD_FIELDS = {
    "move_ids",
    "new_move_ids",
    "date_mode",
    "date",
    "reason",
    "refund_method",
    "journal_id",
    "company_id",
    "available_journal_ids",
    "country_code",
    "residual",
    "currency_id",
    "move_type",
}


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    @api.model
    def _ai_acc_reverse_find_invoice(self, reference):
        """The customer invoice or vendor bill with that id or number."""
        reference = reference.strip()
        if re.fullmatch(rf"\d{{1,{REVERSE_MAX_ID_DIGITS}}}", reference):
            criterion = ("id", "=", int(reference))
        else:
            criterion = ("name", "=", reference)
        moves = self.env["account.move"].search(
            [
                criterion,
                ("move_type", "in", REVERSE_INVOICE_TYPES),
                ("company_id", "in", self._ai_scope_company_ids()),
                "|",
                ("pms_property_id", "=", False),
                ("pms_property_id", "in", self._ai_scope_property_ids()),
            ],
            limit=2,
        )
        if not moves:
            raise UserError(
                _(
                    "No customer invoice or vendor bill found with the id or "
                    "number %s in your companies and properties."
                )
                % reference
            )
        if len(moves) > 1:
            raise UserError(
                _("Several invoices have the number %s: use the id of the invoice.")
                % reference
            )
        return moves

    @api.model
    def _ai_acc_reverse_wizard_values(self, move, mode, reason, date, supplier_ref):
        """Values for the reversal wizard and the localization fields it gets."""
        wizard = self.env["account.move.reversal"].with_context(
            active_model="account.move", active_ids=move.ids
        )
        if not move.journal_id.active:
            raise UserError(
                _(
                    "The journal of the invoice is archived: make this credit "
                    "note in Odoo."
                )
            )
        # The form fills the journal of the invoice, as the wizard computes it.
        values = {
            "refund_method": mode,
            "reason": reason,
            "date_mode": "custom",
            "date": fields.Date.to_string(date),
            "journal_id": move.journal_id.id,
        }
        defaults = wizard.default_get(list(wizard._fields))
        if "supplier_invoice_number_refund" in wizard._fields:
            if supplier_ref:
                values["supplier_invoice_number_refund"] = supplier_ref
            elif defaults.get("supplier_invoice_number_refund_required"):
                raise UserError(
                    _(
                        "The Spanish SII module needs the number of the supplier's "
                        "credit note: give supplier_refund_number."
                    )
                )
        elif supplier_ref:
            raise UserError(
                _(
                    "supplier_refund_number is only used by the Spanish SII "
                    "module, which is not installed."
                )
            )
        merged = dict(defaults, **values)
        magic = set(models.MAGIC_COLUMNS) | {"display_name", "__last_update"}
        missing = sorted(
            name
            for name, field in wizard._fields.items()
            if field.required
            and not field.compute
            and name not in magic
            and not merged.get(name)
        )
        if missing:
            raise UserError(
                _(
                    "The credit note wizard requires fields this tool does not "
                    "fill (%s): make this credit note in Odoo."
                )
                % ", ".join(missing)
            )
        extra = {
            name: merged[name]
            for name in sorted(wizard._fields)
            if name not in REVERSE_STANDARD_FIELDS
            and name not in magic
            and merged.get(name) not in (None, False, "")
        }
        return values, extra

    @api.model
    def _ai_acc_reverse_check_date(self, move, date):
        """The date of the credit note: today by default, never in the future."""
        today = fields.Date.context_today(self)
        date = date or today
        if date > today:
            raise UserError(_("The date of the credit note cannot be in the future."))
        if move.invoice_date and date < move.invoice_date:
            raise UserError(
                _("The date of the credit note is earlier than the invoice date.")
            )
        lock_date = move.company_id._get_user_fiscal_lock_date()
        if date <= lock_date:
            raise UserError(
                _("The date %(date)s is in a locked period (locked up to %(lock)s).")
                % {"date": date, "lock": lock_date}
            )
        return date

    @api.model
    def _ai_acc_reverse_new_partner(self, move, mode, new_partner_id):
        """The partner of the new draft invoice, only for mode=modify."""
        new_partner = self.env["res.partner"]
        if new_partner_id:
            if mode != "modify":
                raise UserError(_("new_partner_id is only accepted with mode=modify."))
            new_partner = self.env["res.partner"].search(
                [
                    ("id", "=", new_partner_id),
                    "|",
                    ("company_id", "=", False),
                    ("company_id", "=", move.company_id.id),
                ]
            )
            if not new_partner:
                raise UserError(_("Partner %s not found.") % new_partner_id)
        return new_partner

    @api.model
    def _ai_acc_reverse_warnings(self, move, posts, new_partner):
        currency = move.currency_id
        residual = move.amount_residual
        warnings = []
        if posts and not currency.is_zero(move.amount_total - residual):
            warnings.append(
                _(
                    "The invoice is partly paid: the credit note settles the "
                    "pending %(pending)s and keeps %(paid)s open in favour of the "
                    "partner (to refund or to offset)."
                )
                % {
                    "pending": amount(residual),
                    "paid": amount(move.amount_total - residual),
                }
            )
        previous = self.env["account.move"].search(
            [("reversed_entry_id", "=", move.id), ("state", "!=", "cancel")]
        )
        if previous:
            warnings.append(
                _("The invoice already has credit notes: %s.")
                % ", ".join(previous.mapped(lambda m: m.name or str(m.id)))
            )
        fiscal_modules = (
            self.env["ir.module.module"]
            .sudo()
            .search(
                [("name", "in", REVERSE_FISCAL_MODULES), ("state", "=", "installed")]
            )
            .mapped("name")
        )
        if fiscal_modules:
            warnings.append(
                _(
                    "Fiscal modules are installed (%s): once posted, the credit "
                    "note may be reported to the tax authority and cannot be "
                    "silently undone."
                )
                % ", ".join(fiscal_modules)
            )
        if new_partner:
            fiscal_position = (
                self.env["account.fiscal.position"]
                .with_company(move.company_id)
                ._get_fiscal_position(new_partner)
            )
            if fiscal_position != move.fiscal_position_id:
                warnings.append(
                    _(
                        "The new partner has another fiscal position: review the "
                        "taxes of the new draft invoice before posting it."
                    )
                )
        return warnings

    @guarded_tool(
        input_schema={
            "invoice": {
                "type": "string",
                "maxLength": 64,
                "description": "Number (for example INV/2026/00012) or numeric id "
                "of the posted customer invoice or vendor bill.",
            },
            "mode": {
                "type": "string",
                "enum": list(REVERSE_MODES),
                "description": "refund: a draft credit note for the whole invoice, "
                "to adjust and post later. cancel: a full credit note, posted and "
                "reconciled with the invoice. modify: like cancel, plus a new "
                "draft copy of the invoice, optionally for another partner.",
            },
            "reason": {
                "type": "string",
                "maxLength": 200,
                "description": "Reason of the credit note (shown in its reference).",
            },
            "date": {
                "type": "string",
                "format": "date",
                "description": "Date of the credit note, YYYY-MM-DD. Default: "
                "today. It cannot be in the future nor before the invoice date.",
            },
            "new_partner_id": {
                "type": "integer",
                "minimum": 1,
                "description": "Only with mode=modify: partner of the new draft "
                "invoice. This is the correct way to change the customer of a "
                "posted invoice.",
            },
            "supplier_refund_number": {
                "type": "string",
                "maxLength": 64,
                "description": "Vendor bills with the Spanish SII module only: "
                "number of the supplier's credit note.",
            },
        },
        output_schema={
            "invoice": {"type": "object"},
            "mode": {"type": "string"},
            "credit_note": {"type": "object"},
            "new_draft_invoice": {"type": "object"},
            "fiscal_fields": {"type": "object"},
        },
        required_inputs=["invoice", "mode", "reason"],
        write=True,
        apply="_apply_acc_invoice_reverse",
    )
    def _ai_acc_invoice_reverse(
        self,
        invoice,
        mode,
        reason,
        date=None,
        new_partner_id=None,
        supplier_refund_number=None,
    ):
        if not self.env.user.has_group("account.group_account_user"):
            raise AccessError(_("This tool needs accounting access."))
        move = self._ai_acc_reverse_find_invoice(invoice)
        reason = " ".join(reason.split())
        if not reason:
            raise UserError(_("Give the reason of the credit note."))
        if move.state != "posted":
            raise UserError(
                _(
                    "The invoice %(number)s is not posted (state: %(state)s): only "
                    "posted invoices can be credited."
                )
                % {"number": move.name or move.id, "state": move.state}
            )
        date = self._ai_acc_reverse_check_date(move, date)
        currency = move.currency_id
        residual = move.amount_residual
        if mode != "refund" and currency.is_zero(residual):
            raise UserError(
                _(
                    "The invoice %s is already paid or credited: only mode=refund "
                    "(a draft credit note) is possible."
                )
                % move.name
            )
        new_partner = self._ai_acc_reverse_new_partner(move, mode, new_partner_id)
        supplier_ref = (supplier_refund_number or "").strip()
        if supplier_ref and move.move_type != "in_invoice":
            raise UserError(
                _("supplier_refund_number is only accepted for vendor bills.")
            )
        values, extra = self._ai_acc_reverse_wizard_values(
            move, mode, reason, date, supplier_ref
        )
        posts = mode != "refund"
        refund_type = "out_refund" if move.move_type == "out_invoice" else "in_refund"
        warnings = self._ai_acc_reverse_warnings(move, posts, new_partner)
        credit_note = {
            "type": refund_type,
            "state": "posted" if posts else "draft",
            "partner": move.partner_id.display_name,
            "date": date,
            "journal": move.journal_id.name,
            "untaxed": amount(move.amount_untaxed),
            "tax": amount(move.amount_tax),
            "total": amount(move.amount_total),
            "reconciled_with_invoice": amount(residual) if posts else 0.0,
        }
        new_draft = {}
        if mode == "modify":
            partner = new_partner or move.partner_id
            new_draft = {
                "type": move.move_type,
                "state": "draft",
                "partner_id": partner.id,
                "partner": partner.display_name,
                "partner_changed": bool(new_partner and new_partner != move.partner_id),
                "date": date,
                "lines": len(move.invoice_line_ids),
                "total_before_review": amount(move.amount_total),
            }
        summary = {
            "refund": _("Create a draft credit note for the invoice %s."),
            "cancel": _(
                "Create, post and reconcile a full credit note for the invoice %s."
            ),
            "modify": _(
                "Create, post and reconcile a full credit note for the invoice %s "
                "and a new draft copy of it."
            ),
        }[mode] % move.name
        return {
            "summary": summary,
            "definitions": [
                _(
                    "Made with the standard credit note wizard of Odoo, as the "
                    "user. refund: the credit note stays in draft for the whole "
                    "amount; edit its lines and post it in Odoo. cancel and "
                    "modify: the credit note is posted and reconciled with the "
                    "invoice, which is left with nothing pending."
                ),
                _(
                    "modify: the new draft invoice copies the lines of the "
                    "original; it is not posted. fiscal_fields: values of the "
                    "fields added to the wizard by localization modules."
                ),
            ],
            "warnings": warnings,
            "result": {
                "invoice": {
                    "id": move.id,
                    "number": move.name,
                    "type": move.move_type,
                    "partner": move.partner_id.display_name,
                    "company": move.company_id.name,
                    "property": move.pms_property_id.name or None,
                    "invoice_date": move.invoice_date,
                    "currency": currency.name,
                    "total": amount(move.amount_total),
                    "pending": amount(residual),
                    "payment_state": move.payment_state,
                },
                "mode": mode,
                "reason": reason,
                "credit_note": credit_note,
                "new_draft_invoice": new_draft,
                "fiscal_fields": extra,
            },
            "plan": {
                "move_id": move.id,
                "wizard_values": values,
                "new_partner_id": new_partner.id,
                "posts": posts,
            },
        }

    def _apply_acc_invoice_reverse(self, plan, **_arguments):
        move = self.env["account.move"].browse(plan["move_id"])
        if move.state != "posted":
            raise UserError(_("The invoice is no longer posted; nothing was done."))
        before = move.reversal_move_id
        wizard = (
            self.env["account.move.reversal"]
            .with_context(active_model="account.move", active_ids=move.ids)
            .create(dict(plan["wizard_values"]))
        )
        wizard.reverse_moves()
        credit_notes = move.reversal_move_id - before
        new_drafts = wizard.new_move_ids - credit_notes
        if plan["new_partner_id"]:
            new_drafts.write({"partner_id": plan["new_partner_id"]})
        expected_state = "posted" if plan["posts"] else "draft"
        expected_drafts = 1 if plan["wizard_values"]["refund_method"] == "modify" else 0
        if (
            len(credit_notes) != 1
            or credit_notes.state != expected_state
            or len(new_drafts) != expected_drafts
            or (plan["posts"] and not move.currency_id.is_zero(move.amount_residual))
        ):
            raise UserError(
                _(
                    "The credit note did not come out as previewed; nothing was "
                    "saved. Check the invoice in Odoo."
                )
            )
        return {
            "summary": _("Credit note %(credit)s created for the invoice %(invoice)s.")
            % {"credit": credit_notes.name or credit_notes.id, "invoice": move.name},
            "result": {
                "invoice": {
                    "id": move.id,
                    "number": move.name,
                    "pending": amount(move.amount_residual),
                    "payment_state": move.payment_state,
                },
                "credit_note": {
                    "id": credit_notes.id,
                    "number": credit_notes.name,
                    "type": credit_notes.move_type,
                    "state": credit_notes.state,
                    "date": credit_notes.date,
                    "total": amount(credit_notes.amount_total),
                    "pending": amount(credit_notes.amount_residual),
                },
                "new_draft_invoice": {
                    "id": new_drafts.id,
                    "type": new_drafts.move_type,
                    "state": new_drafts.state,
                    "partner_id": new_drafts.partner_id.id,
                    "partner": new_drafts.partner_id.display_name,
                    "total": amount(new_drafts.amount_total),
                }
                if new_drafts
                else {},
            },
        }
