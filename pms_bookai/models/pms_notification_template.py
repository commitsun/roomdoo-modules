import logging
import re
from html import unescape

try:
    from whatsapp_formatter import convert_html_to_whatsapp
except ImportError:  # pragma: no cover - fallback when dependency is not installed
    convert_html_to_whatsapp = None

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)
BODY_PLACEHOLDER_REGEX = re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*}}")
HTML_BR_REGEX = re.compile(r"<br\s*/?>", flags=re.IGNORECASE)
HTML_P_CLOSE_REGEX = re.compile(r"</p\s*>", flags=re.IGNORECASE)
HTML_TAG_REGEX = re.compile(r"<[^>]+>")


class PmsNotificationTemplate(models.Model):
    _inherit = "pms.notification.template"

    # ---------------------------------------------------------------------
    # BookAI WhatsApp configuration
    # ---------------------------------------------------------------------
    channel_bookai_whatsapp_enabled = fields.Boolean(
        string="Enable BookAI WhatsApp",
        compute="_compute_channel_bookai_whatsapp_enabled",
        store=False,
        help="Technical flag used by the UI to show BookAI WhatsApp fields.",
    )

    bookai_template_code = fields.Char(
        string="BookAI Template Code",
        help="BookAI template code (e.g. booking_confirmation_v1).",
    )

    # Rendered using mail engine syntax: {{ object.xxx }}, if/for blocks, etc.
    bookai_recipient_phone_tmpl = fields.Text(
        string="Recipient Phone (Template)",
        help="Expression that returns the recipient "
        "phone (E.164 preferred, e.g. +34...).",
    )
    bookai_language_tmpl = fields.Text(
        string="Language (Template)",
        help="Expression that returns the template language code (e.g. es, en).",
    )
    bookai_recipient_name_tmpl = fields.Text(
        string="Recipient Display Name (Template)",
        help="Expression that returns an optional recipient display name.",
    )
    bookai_origin_folio_id_tmpl = fields.Text(
        string="Origin Folio ID (Template)",
        help=(
            "Expression that returns the origin pms.folio ID "
            "(required by BookAI payload)."
        ),
    )

    bookai_param_ids = fields.One2many(
        "pms.notification.template.bookai.param",
        "template_id",
        string="BookAI Parameters",
        help=("Parameters injected into BookAI WhatsApp template."),
    )
    body = fields.Text(
        string="Body",
        translate=True,
        help=(
            "WhatsApp message body. Supports QWeb tags (t-if, t-foreach, ...) and "
            "placeholder keys like {{ buyer_name }} defined in BookAI Parameters."
        ),
    )

    # ---------------------------------------------------------------------
    # Computed flags
    # ---------------------------------------------------------------------
    def _compute_channel_bookai_whatsapp_enabled(self):
        for rec in self:
            rec.channel_bookai_whatsapp_enabled = bool(rec.bookai_template_code)

    # ---------------------------------------------------------------------
    # Rendering helpers (mail engine)
    # ---------------------------------------------------------------------
    def _bookai_render_inline(
        self, template_src, record, lang=None, extra_context=None
    ):
        """
        Render an inline template string using Odoo's mail rendering engine.

        Supports the same syntax as mail templates:
        - {{ expression }} inline expressions
        """
        self.ensure_one()
        if not template_src:
            return ""

        ctx = dict(self.env.context)
        if lang:
            ctx["lang"] = lang
        if extra_context:
            ctx.update(extra_context)
        rendered = (
            self.env["mail.template"]
            .with_context(ctx)
            ._render_template(template_src, record._name, [record.id])
        )
        return (rendered or {}).get(record.id, "") or ""

    def _bookai_render_qweb(self, template_src, record, lang=None, extra_context=None):
        """Render raw QWeb source against a record."""
        self.ensure_one()
        if not template_src:
            return ""

        ctx = dict(self.env.context)
        if lang:
            ctx["lang"] = lang
        if extra_context and extra_context.get("tz"):
            ctx["tz"] = extra_context.get("tz")

        rendered = (
            self.env["mail.template"]
            .with_context(ctx)
            ._render_template(
                template_src,
                record._name,
                [record.id],
                engine="qweb",
                add_context=extra_context or {},
            )
        )
        return (rendered or {}).get(record.id, "") or ""

    def _bookai_to_whatsapp_text(self, content):
        """
        Convert rendered HTML/QWeb output into WhatsApp-safe text.

        Uses py-whatsapp-formatter when available. Falls back to a basic
        HTML-to-text conversion for non-container executions.
        """
        self.ensure_one()
        text = content or ""
        if not text:
            return ""

        if convert_html_to_whatsapp:
            try:
                text = convert_html_to_whatsapp(text)
            except Exception:
                _logger.exception(
                    "whatsapp_formatter failed. Falling back to basic HTML stripping."
                )
                text = HTML_TAG_REGEX.sub(
                    "",
                    HTML_P_CLOSE_REGEX.sub("\n\n", HTML_BR_REGEX.sub("\n", text)),
                )
        else:
            text = HTML_TAG_REGEX.sub(
                "",
                HTML_P_CLOSE_REGEX.sub("\n\n", HTML_BR_REGEX.sub("\n", text)),
            )

        text = unescape(text)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _bookai_render_qweb_to_whatsapp(
        self, template_src, record, lang=None, extra_context=None
    ):
        self.ensure_one()
        rendered = self._bookai_render_qweb(
            template_src, record, lang=lang, extra_context=extra_context
        )
        return self._bookai_to_whatsapp_text(rendered)

    def _bookai_build_parameters(self, record, lang=None, tz=None):
        """Return a dict of parameters for BookAI from bookai_param_ids."""
        self.ensure_one()
        params = {}
        for param in self.bookai_param_ids.sorted(lambda x: (x.sequence, x.id)):
            params[param.key] = param.get_value_for_record(record, lang=lang, tz=tz)
        return params

    def _bookai_get_active_lang_code(self):
        self.ensure_one()
        Lang = self.env["res.lang"]

        for candidate in (self.env.context.get("lang"), self.env.user.lang):
            if not candidate:
                continue
            lang_rec = Lang.search(
                [
                    ("active", "=", True),
                    "|",
                    ("code", "=", candidate),
                    ("iso_code", "=", candidate),
                ],
                limit=1,
            )
            if lang_rec:
                return lang_rec.code or ""

        fallback_lang = Lang.search([("active", "=", True)], limit=1)
        return fallback_lang.code or ""

    def _bookai_render_body(self, record):
        self.ensure_one()
        lang = self._bookai_get_active_lang_code()
        tz = (self.env.context.get("tz") or self.env.user.tz or "UTC").strip()
        params = self._bookai_build_parameters(record, lang=lang, tz=tz)
        body_rendered = self._render_body_with_params(
            record,
            params,
            lang=lang,
            tz=tz,
        )
        return body_rendered, params, lang, tz

    def _render_body_with_params(self, record, params, lang=None, tz=None):
        self.ensure_one()
        body_template = (self.body or "").strip()
        if not body_template:
            return ""

        param_values = {
            str(key): "" if value is False or value is None else str(value)
            for key, value in (params or {}).items()
        }

        def _replace_placeholder(match):
            key = match.group(1)
            return f"<t t-out=\"bookai_params.get('{key}', '')\"/>"

        body_qweb = BODY_PLACEHOLDER_REGEX.sub(_replace_placeholder, body_template)
        render_ctx = {"bookai_params": param_values}
        if lang:
            render_ctx["bookai_lang"] = lang
        if tz:
            render_ctx["tz"] = tz

        return self._bookai_render_qweb_to_whatsapp(
            body_qweb,
            record,
            lang=lang,
            extra_context=render_ctx,
        )

    # ---------------------------------------------------------------------
    # Constraints
    # ---------------------------------------------------------------------
    @api.constrains(
        "bookai_template_code",
        "bookai_recipient_phone_tmpl",
        "bookai_language_tmpl",
        "bookai_origin_folio_id_tmpl",
    )
    def _check_bookai_required_fields(self):
        """
        If this template is used by any WhatsApp rules, ensure mandatory fields exist.
        """
        Rule = self.env["pms.property.notification.rule"]
        for tmpl in self:
            rule_count = Rule.search_count(
                [
                    ("template_id", "=", tmpl.id),
                    ("channel", "=", "bookai_whatsapp"),
                    ("active", "=", True),
                ]
            )
            if not rule_count:
                continue

            missing = []
            if not tmpl.bookai_template_code:
                missing.append(_("BookAI Template Code"))
            if not (tmpl.bookai_recipient_phone_tmpl or "").strip():
                missing.append(_("Recipient Phone (Template)"))
            if not (tmpl.bookai_language_tmpl or "").strip():
                missing.append(_("Language (Template)"))
            if not (tmpl.bookai_origin_folio_id_tmpl or "").strip():
                missing.append(_("Origin Folio ID (Template)"))

            if missing:
                raise ValidationError(
                    _(
                        "Template '%s' is used by BookAI "
                        "WhatsApp rules but is missing:\n- %s"
                    )
                    % (tmpl.display_name, "\n- ".join(missing))
                )

    @api.constrains("body", "bookai_param_ids")
    def _check_bookai_body_keys(self):
        for template in self:
            body = template.body or ""
            keys_in_body = set(BODY_PLACEHOLDER_REGEX.findall(body))
            if not keys_in_body:
                continue

            available_keys = set(template.bookai_param_ids.mapped("key"))
            unknown_keys = sorted(keys_in_body - available_keys)
            if unknown_keys:
                raise ValidationError(
                    _(
                        "Template '%(template)s' has unknown placeholders in body: "
                        "%(keys)s. Define those keys in BookAI Parameters."
                    )
                    % {
                        "template": template.display_name,
                        "keys": ", ".join(unknown_keys),
                    }
                )
