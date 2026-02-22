import logging

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class PmsNotificationTemplateBookaiParam(models.Model):
    _name = "pms.notification.template.bookai.param"
    _description = "BookAI Template Parameter"
    _order = "sequence, id"

    template_id = fields.Many2one(
        "pms.notification.template",
        required=True,
        ondelete="cascade",
        index=True,
    )

    # Keep this name to avoid breaking existing views/domains.
    target_model_name = fields.Char(
        related="template_id.target_model_name",
        store=True,
        readonly=True,
    )

    sequence = fields.Integer(default=10)

    key = fields.Char(
        string="Key",
        required=True,
        help="Parameter key sent to BookAI (e.g. buyer_name, hotel_name...).",
    )
    description = fields.Char(
        string="Description",
        help="Optional description shown to API clients.",
    )

    value_type = fields.Selection(
        [
            ("literal", "Literal"),
            ("field", "Field"),
            ("inline", "Inline Template"),
            ("qweb", "QWeb Template"),
        ],
        string="Value Type",
        required=True,
        default="inline",
        help="How the value is produced.",
    )

    value_literal = fields.Text(
        string="Literal Value",
        translate=True,
        help="Used when Value Type = Literal.",
    )

    field_id = fields.Many2one(
        "ir.model.fields",
        string="Field",
        domain="[('model', '=', target_model_name),"
        " ('ttype', 'not in', ('one2many','many2many','binary'))]",
        help="Used when Value Type = Field.",
    )

    value_inline_tmpl = fields.Text(
        string="Inline Template",
        translate=True,
        help="Template source used when Value Type is Inline or QWeb.\n"
        "Inline mode supports {{ ... }} expressions.\n"
        "QWeb mode supports <t t-out>, t-if, t-foreach, etc.\n"
        "Example: {{ object.partner_name or "
        "(object.partner_id and object.partner_id.name) or '' }}",
    )

    _sql_constraints = [
        (
            "bookai_param_key_uniq",
            "unique(template_id, key)",
            "Parameter key must be unique per template.",
        ),
    ]

    @api.onchange("template_id")
    def _onchange_template_id_reset_field(self):
        for rec in self:
            rec.field_id = False

    @api.constrains("value_type", "field_id", "value_inline_tmpl", "value_literal")
    def _check_value_sources(self):
        for rec in self:
            if rec.value_type == "field" and not rec.field_id:
                raise ValidationError(
                    _("BookAI param '%s': field is required.") % rec.key
                )
            if (
                rec.value_type in ("inline", "qweb")
                and not (rec.value_inline_tmpl or "").strip()
            ):
                raise ValidationError(
                    _("BookAI param '%s': template source is required.") % rec.key
                )
            if rec.value_type == "literal" and not (rec.value_literal or "").strip():
                raise ValidationError(
                    _("BookAI param '%s': literal value is required.") % rec.key
                )

    def get_value_for_record(self, record, lang=None, tz=None):
        """
        Compute the param value for a given origin record.

        Rendering uses the notification template helper, so syntax/behavior matches
        the rest of the BookAI template fields.
        """
        self.ensure_one()
        param = self

        # Apply locale context (lang + tz) so date/datetime formatting is localized
        if lang or tz:
            ctx = dict(record.env.context)
            if lang:
                ctx["lang"] = lang
            if tz:
                ctx["tz"] = tz
            param = self.with_context(ctx)
            record = record.with_context(ctx)

        if param.value_type == "literal":
            return (param.value_literal or "").strip()

        if param.value_type == "field":
            if not param.field_id:
                return ""
            val = record[param.field_id.name]
            if val is False or val is None:
                return ""

            if param.field_id.ttype == "many2one":
                return val.display_name or ""

            # ✅ Localized date/datetime (uses record.env + context lang/tz)
            if param.field_id.ttype == "date":
                from odoo.tools.misc import format_date

                return format_date(record.env, val) or ""
            if param.field_id.ttype == "datetime":
                from odoo.tools.misc import format_datetime

                return format_datetime(record.env, val) or ""

            return str(val)

        if param.value_type == "qweb":
            extra_ctx = {}
            if tz:
                extra_ctx["tz"] = tz
            if lang:
                extra_ctx["bookai_lang"] = lang
            return param.template_id._bookai_render_qweb_to_whatsapp(
                param.value_inline_tmpl,
                record,
                lang=lang,
                extra_context=extra_ctx or None,
            ).strip()

        # inline (rendered through mail engine, will also use lang/tz via context)
        return param.template_id._bookai_render_inline(
            param.value_inline_tmpl,
            record,
            lang=lang,
            extra_context={"tz": tz} if tz else None,
        ).strip()
