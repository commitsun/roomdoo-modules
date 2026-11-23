import re
from datetime import date

from odoo import _, models
from odoo.exceptions import UserError
from odoo.tools.mail import html2plaintext, plaintext2html

from odoo.addons.pms_ai_tool.tools import guarded_tool

MAX_RECORDS = 20
MAX_FIELDS = 10
MAX_VALUE_CHARS = 2000
# Longest numeric text taken as a record id; anything longer is a name.
MAX_ID_DIGITS = 12

# Closed allowlist: the only models and fields the tool can change. Fields
# missing in the database (payment_mode_id without its module) are refused.
EDITABLE_FIELDS = {
    "account.move": (
        "partner_id",
        "ref",
        "payment_reference",
        "invoice_date",
        "date",
        "invoice_date_due",
        "partner_bank_id",
        "narration",
        "to_check",
        "payment_mode_id",
    ),
    "account.payment": ("date", "ref", "partner_id", "partner_bank_id"),
    "res.partner": (
        "name",
        "vat",
        "email",
        "phone",
        "mobile",
        "street",
        "street2",
        "zip",
        "city",
        "state_id",
        "country_id",
        "property_payment_term_id",
        "property_supplier_payment_term_id",
    ),
    "res.partner.bank": ("acc_number", "bank_id"),
}
# Fields that only exist for invoices and bills, not for other entries.
INVOICE_ONLY_FIELDS = {
    "invoice_date",
    "invoice_date_due",
    "payment_reference",
    "partner_bank_id",
    "payment_mode_id",
}
# Fields that cannot be emptied even if the database allows it.
NOT_EMPTY_FIELDS = {"name", "acc_number", "date", "partner_id"}
SUPPORTED_TYPES = {
    "char",
    "text",
    "html",
    "date",
    "boolean",
    "float",
    "selection",
    "many2one",
}
TRUE_WORDS = {"true", "1", "yes"}
FALSE_WORDS = {"false", "0", "no"}


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    def _ai_edit_fields(self, model, field_names):
        """The fields to change, all of them in the allowlist of the model."""
        allowed = EDITABLE_FIELDS.get(model)
        if allowed is None:
            raise UserError(
                _("The model %(model)s cannot be changed. Allowed: %(allowed)s.")
                % {"model": model, "allowed": ", ".join(EDITABLE_FIELDS)}
            )
        if len(set(field_names)) != len(field_names):
            raise UserError(_("Each field can appear only once."))
        Model = self.env[model]
        refused = [
            name
            for name in field_names
            if name not in allowed
            or name not in Model._fields
            or Model._fields[name].type not in SUPPORTED_TYPES
        ]
        if refused:
            raise UserError(
                _(
                    "These fields cannot be changed on %(model)s: %(refused)s. "
                    "Allowed: %(allowed)s."
                )
                % {
                    "model": model,
                    "refused": ", ".join(refused),
                    "allowed": ", ".join(
                        name for name in allowed if name in Model._fields
                    ),
                }
            )
        return [Model._fields[name] for name in field_names]

    def _ai_edit_company_partner_ids(self):
        return set(self.env["res.company"].sudo().search([]).partner_id.ids)

    def _ai_edit_refusal(self, record, field_names, scope):
        """Why the record cannot be changed by the tool, or None."""
        model = record._name
        if model in ("account.move", "account.payment"):
            move = record if model == "account.move" else record.move_id
            if move.company_id.id not in scope["company_ids"]:
                return _("another company")
            if move.pms_property_id and (
                move.pms_property_id.id not in scope["property_ids"]
            ):
                return _("another property")
            if move.state != "draft":
                return _("not in draft")
            if model == "account.move":
                if move.payment_id or move.statement_line_id:
                    return _("entry of a payment or bank statement line")
                invoice_only = INVOICE_ONLY_FIELDS & set(field_names)
                if invoice_only and not move.is_invoice(include_receipts=True):
                    return _("not an invoice or bill")
                if "invoice_date_due" in field_names and move.invoice_payment_term_id:
                    return _("has payment terms, which set the due date")
            return None
        if record.company_id and record.company_id.id not in scope["company_ids"]:
            return _("another company")
        partner = record if model == "res.partner" else record.partner_id
        if partner.id in scope["company_partner_ids"]:
            return _("partner of a company of the database")
        if partner.sudo().with_context(active_test=False).user_ids:
            return _("partner of a user")
        return None

    def _ai_edit_records(self, model, record_ids, field_names):
        """The records to change, if the user can change them all."""
        record_ids = list(dict.fromkeys(record_ids))
        records = self.env[model].with_context(active_test=False).browse(record_ids)
        missing = set(record_ids) - set(records.exists().ids)
        if missing:
            raise UserError(
                _("Records of %(model)s not found: %(ids)s.")
                % {"model": model, "ids": ", ".join(map(str, sorted(missing)))}
            )
        # As the user: access rights and record rules apply.
        records.check_access_rights("write")
        records.check_access_rule("write")
        scope = {
            "company_ids": self._ai_scope_company_ids(),
            "property_ids": self._ai_scope_property_ids(),
            "company_partner_ids": self._ai_edit_company_partner_ids(),
        }
        refused = []
        for record in records:
            reason = self._ai_edit_refusal(record, field_names, scope)
            if reason:
                refused.append(f"{record.id} ({reason})")
        if refused:
            raise UserError(
                _("These %(model)s records cannot be changed: %(refused)s.")
                % {"model": model, "refused": "; ".join(refused)}
            )
        return records

    def _ai_edit_target(self, field, text):
        """The record of a many2one, by id or by exact and unique name."""
        Comodel = self.env[field.comodel_name]
        if re.fullmatch(rf"\d{{1,{MAX_ID_DIGITS}}}", text):
            target = Comodel.search([("id", "=", int(text))], limit=1)
        else:
            target = Comodel.search([(Comodel._rec_name or "name", "=", text)], limit=2)
            if len(target) > 1:
                raise UserError(
                    _("Several %(model)s records are named %(value)s: give the id.")
                    % {"model": field.comodel_name, "value": text}
                )
        if not target:
            raise UserError(
                _("No %(model)s record found with the id or name %(value)s.")
                % {"model": field.comodel_name, "value": text}
            )
        # A value kept per company must belong to the company it is set for.
        company = target.company_id if "company_id" in target._fields else None
        if field.company_dependent and company and company != self.env.company:
            raise UserError(
                _("%(value)s belongs to another company than %(company)s.")
                % {"value": target.display_name, "company": self.env.company.name}
            )
        return target

    def _ai_edit_convert(self, field, text):
        """The value to write in ``field`` from the text given."""
        text = text.strip()
        if not text and field.type != "boolean":
            if field.required or field.name in NOT_EMPTY_FIELDS:
                raise UserError(_("The field %s cannot be emptied.") % field.name)
            return False
        kind = field.type
        if kind in ("char", "text"):
            return text
        if kind == "html":
            return plaintext2html(text)
        if kind == "date":
            try:
                return date.fromisoformat(text)
            except ValueError:
                raise UserError(
                    _("%s must be a date in YYYY-MM-DD format.") % field.name
                ) from None
        if kind == "boolean":
            if text.lower() in TRUE_WORDS:
                return True
            if text.lower() in FALSE_WORDS:
                return False
            raise UserError(_("%s must be true or false.") % field.name)
        if kind == "float":
            try:
                return float(text)
            except ValueError:
                raise UserError(
                    _("%s must be a number with a dot as decimal separator.")
                    % field.name
                ) from None
        if kind == "selection":
            keys = [key for key, _label in field._description_selection(self.env)]
            if text not in keys:
                raise UserError(
                    _("%(field)s must be one of: %(keys)s.")
                    % {"field": field.name, "keys": ", ".join(keys)}
                )
            return text
        if kind == "many2one":
            return self._ai_edit_target(field, text)
        raise UserError(_("The field %s cannot be changed.") % field.name)

    def _ai_edit_display(self, field, value):
        """A value of ``field`` as shown in the preview."""
        if field.type == "many2one":
            return {"id": value.id, "name": value.display_name} if value else None
        if field.type == "boolean":
            return bool(value)
        if not value:
            return None
        if field.type == "html":
            return html2plaintext(value) or None
        return value

    def _ai_edit_matches(self, field, stored, expected):
        """Whether the value stored is the one planned.

        Odoo may normalise a text on write (a VAT number loses its spaces and
        dots), which is accepted; any other difference is not.
        """
        if stored == expected:
            return True
        if field.type in ("char", "text") and stored and expected:
            return (
                re.sub(r"[\s./-]", "", stored).upper()
                == re.sub(r"[\s./-]", "", expected).upper()
            )
        return False

    def _ai_edit_write_value(self, field, value):
        return value.id if field.type == "many2one" and value else value

    def _ai_edit_warnings(self, model, field_names):
        warnings = []
        if model == "account.move" and {"partner_id", "invoice_date"} & set(
            field_names
        ):
            warnings.append(
                _(
                    "As in the form, Odoo recomputes what depends on the changed "
                    "fields: the partner sets the fiscal position, the payment "
                    "terms and the bank account; the bill date sets the "
                    "accounting date and the due date. The confirmation lists "
                    "any other field that changed."
                )
            )
        company_dependent = [
            name
            for name in field_names
            if self.env[model]._fields[name].company_dependent
        ]
        if company_dependent:
            warnings.append(
                _(
                    "%(fields)s are kept per company: the value is set for the "
                    "company %(company)s."
                )
                % {
                    "fields": ", ".join(company_dependent),
                    "company": self.env.company.name,
                }
            )
        return warnings

    @guarded_tool(
        input_schema={
            "model": {
                "type": "string",
                "enum": list(EDITABLE_FIELDS),
                "description": "Model of the records to change.",
            },
            "record_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "minItems": 1,
                "maxItems": MAX_RECORDS,
                "description": f"Ids of the records, at most {MAX_RECORDS}.",
            },
            "fields": {
                "type": "array",
                "items": {"type": "string", "maxLength": 64},
                "minItems": 1,
                "maxItems": MAX_FIELDS,
                "description": "Technical names of the fields to change; values "
                "go in the same order.",
            },
            "values": {
                "type": "array",
                "items": {"type": "string", "maxLength": MAX_VALUE_CHARS},
                "minItems": 1,
                "maxItems": MAX_FIELDS,
                "description": "New value of each field, as text: dates "
                "YYYY-MM-DD, true or false, numbers with a dot, the key of a "
                "selection, and a related record by id or by its exact name. "
                "An empty text empties the field when allowed.",
            },
        },
        output_schema={
            "model": {"type": "string"},
            "records": {"type": "array", "items": {"type": "object"}},
            "to_change": {"type": "integer"},
        },
        required_inputs=["model", "record_ids", "fields", "values"],
        write=True,
        apply="_apply_acc_record_edit",
        log_result=False,
    )
    def _ai_acc_record_edit(self, model, record_ids, fields, values):
        if len(fields) != len(values):
            raise UserError(_("fields and values must have the same length."))
        field_objs = self._ai_edit_fields(model, fields)
        records = self._ai_edit_records(model, record_ids, fields)
        new_values = {
            field.name: self._ai_edit_convert(field, text)
            for field, text in zip(field_objs, values, strict=True)
        }
        rows = []
        changes = {}
        for record in records:
            row_changes = []
            for field in field_objs:
                old = self._ai_edit_display(field, record[field.name])
                new = self._ai_edit_display(field, new_values[field.name])
                row_changes.append(
                    {
                        "field": field.name,
                        "label": field._description_string(self.env),
                        "old": old,
                        "new": new,
                        "changes": old != new,
                    }
                )
                if old != new:
                    changes.setdefault(record.id, {})[
                        field.name
                    ] = self._ai_edit_write_value(field, new_values[field.name])
            rows.append(
                {
                    "id": record.id,
                    "name": record.display_name,
                    "will_change": record.id in changes,
                    "fields": row_changes,
                }
            )
        if not changes:
            raise UserError(
                _("Nothing to change: the records already have these values.")
            )
        return {
            "summary": _("%(count)s %(model)s records will change: %(fields)s.")
            % {"count": len(changes), "model": model, "fields": ", ".join(fields)},
            "definitions": [
                _(
                    "Each record shows the current value (old) and the new one of "
                    "every field; only the fields that change are written, as the "
                    "user and in one transaction."
                ),
                _(
                    "Only draft invoices, bills and payments of the user's "
                    "companies and properties, and partners and bank accounts "
                    "of those companies, can be changed, on a closed list of "
                    "fields. Partners of users or of companies of the database "
                    "are refused."
                ),
                _("To undo it, run the tool again with the old values."),
            ],
            "warnings": self._ai_edit_warnings(model, fields),
            "result": {
                "model": model,
                "records": rows,
                "to_change": len(changes),
            },
            "plan": {"model": model, "changes": changes},
        }

    def _apply_acc_record_edit(self, plan, model, record_ids, fields, values):
        if plan["model"] != model:
            raise UserError(_("The confirmation does not match the model."))
        field_objs = {
            field.name: field for field in self._ai_edit_fields(model, fields)
        }
        changes = plan["changes"]
        if not set(changes) <= set(record_ids):
            raise UserError(_("The confirmation does not match the records."))
        records = self._ai_edit_records(model, list(changes), fields)
        tracked = [name for name in EDITABLE_FIELDS[model] if name in records._fields]

        def snapshot(record):
            return {
                name: self._ai_edit_display(record._fields[name], record[name])
                for name in tracked
            }

        before = {record.id: snapshot(record) for record in records}
        for record in records:
            vals = changes[record.id]
            if not set(vals) <= set(field_objs):
                raise UserError(_("The confirmation does not match the fields."))
            record.write(vals)
        self.env.flush_all()
        records.invalidate_recordset()
        rows = []
        for record in records:
            after = snapshot(record)
            vals = changes[record.id]
            for name, value in vals.items():
                field = field_objs[name]
                expected = (
                    value
                    if field.type != "many2one"
                    else self.env[field.comodel_name].browse(value or [])
                )
                if not self._ai_edit_matches(
                    field, after[name], self._ai_edit_display(field, expected)
                ):
                    raise UserError(
                        _(
                            "Odoo did not keep the value of %(field)s on the "
                            "record %(id)s; nothing was saved."
                        )
                        % {"field": name, "id": record.id}
                    )
            rows.append(
                {
                    "id": record.id,
                    "name": record.display_name,
                    "changed": {name: after[name] for name in vals},
                    "also_changed": {
                        name: {"old": before[record.id][name], "new": after[name]}
                        for name in tracked
                        if name not in vals and after[name] != before[record.id][name]
                    },
                }
            )
        return {
            "summary": _("%(count)s %(model)s records changed.")
            % {"count": len(rows), "model": model},
            "result": {"model": model, "records": rows},
        }
