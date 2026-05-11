import json
import logging
import re
from html import unescape

import requests

try:
    from whatsapp_formatter import convert_html_to_whatsapp
except ImportError:  # pragma: no cover
    convert_html_to_whatsapp = None

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

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
    bookai_category = fields.Selection(
        [
            ("UTILITY", "Utility"),
            ("MARKETING", "Marketing"),
            ("AUTHENTICATION", "Authentication"),
        ],
        string="WhatsApp Category",
        default="UTILITY",
        help="Meta template category. Cannot be changed after " "first sync.",
    )
    bookai_translation_ids = fields.One2many(
        "bookai.whatsapp.translation",
        "template_id",
        string="WhatsApp Translations",
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
    bookai_example_record_id = fields.Integer(
        string="Example Record ID",
        help="ID of the record to render parameter examples.",
    )
    bookai_example_record_name = fields.Char(
        string="Example Record",
        compute="_compute_example_record_name",
    )
    bookai_body_example = fields.Text(
        string="Body Example",
        compute="_compute_bookai_body_example",
        help="Preview of the body with example param values.",
    )

    bookai_header_text = fields.Text(
        string="Header Text",
        translate=True,
        help="Optional header shown above the message "
        "in WhatsApp. May contain placeholders.",
    )
    bookai_footer_text = fields.Text(
        string="Footer Text",
        translate=True,
        help="Optional footer shown below the message "
        "in WhatsApp. Meta does not allow placeholders.",
    )
    bookai_button_texts = fields.Text(
        string="Button Texts",
        translate=True,
        help="Optional JSON list of buttons. Example: "
        '[{"type":"URL","text":"Check-in","url":"https://..."}]',
    )

    # ---------------------------------------------------------------------
    # Computed flags & selection helpers
    # ---------------------------------------------------------------------
    def _compute_channel_bookai_whatsapp_enabled(self):
        for rec in self:
            rec.channel_bookai_whatsapp_enabled = bool(rec.bookai_template_code)

    def _compute_example_record_name(self):
        for rec in self:
            if not rec.bookai_example_record_id or not rec.model_id:
                rec.bookai_example_record_name = ""
                continue
            model_name = rec.model_id.model
            if model_name not in rec.env:
                rec.bookai_example_record_name = ""
                continue
            record = rec.env[model_name].browse(rec.bookai_example_record_id).exists()
            rec.bookai_example_record_name = record.display_name if record else ""

    def action_generate_examples(self):
        """Fill example_value on each param by rendering
        against the selected example record."""
        self.ensure_one()
        if not self.bookai_example_record_id or not self.model_id:
            raise UserError(_("Select an Example Record first."))
        model_name = self.model_id.model
        record = self.env[model_name].browse(self.bookai_example_record_id).exists()
        if not record:
            raise UserError(
                _("Record %s(%s) not found.")
                % (model_name, self.bookai_example_record_id)
            )
        lang = self._bookai_get_active_lang_code()
        tz = self.env.user.tz or "UTC"
        params = self._bookai_build_parameters(record, lang=lang, tz=tz)
        for param in self.bookai_param_ids:
            val = params.get(param.key, "")
            param.write(
                {
                    "example_value": (str(val)[:200] if val else param.key),
                }
            )

    def _compute_bookai_body_example(self):
        for rec in self:
            body = (rec.body or "").strip()
            if not body or not rec.bookai_param_ids:
                rec.bookai_body_example = ""
                continue
            example_map = {
                p.key: p.example_value or p.key
                for p in rec.bookai_param_ids.sorted("sequence")
            }

            def _replace(match, _map=example_map):
                return _map.get(match.group(1), match.group(0))

            rec.bookai_body_example = BODY_PLACEHOLDER_REGEX.sub(_replace, body)

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
        if not template_src or not template_src.strip():
            return ""
        # Guard against empty expressions like "{{ }}"
        stripped = template_src.strip()
        if stripped == "{{ }}" or stripped == "{{}}":
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
    # Sync to BookAI
    # ---------------------------------------------------------------------
    _SYNC_TIMEOUT = 15

    def _get_bookai_headers(self):
        icp = self.env["ir.config_parameter"].sudo()
        base_url = icp.get_param("pms_bookai.api_endpoint", "")
        token = icp.get_param("pms_bookai.api_token", "")
        if not base_url or not token:
            raise UserError(
                _(
                    "Configure BooKAI Base URL and Bearer Token "
                    "in Settings before syncing."
                )
            )
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        return base_url, headers

    def action_sync_to_bookai(self):
        """Sync this WhatsApp template to BookAI."""
        self.ensure_one()
        if not self.bookai_template_code:
            raise UserError(_("Set a BookAI Template Code before syncing."))
        # Auto-create/sync translations from body i18n
        self._sync_translations_from_i18n()
        base_url, headers = self._get_bookai_headers()
        payload = self._build_bookai_template_payload()
        endpoint = base_url.rstrip("/") + "/api/v1/whatsapp/templates"
        try:
            resp = requests.post(
                endpoint,
                data=json.dumps(payload),
                headers=headers,
                timeout=self._SYNC_TIMEOUT,
            )
            if resp.status_code == 409:
                patch_url = f"{endpoint}/{self.bookai_template_code}"
                resp = requests.patch(
                    patch_url,
                    data=json.dumps({"translations": payload["translations"]}),
                    headers=headers,
                    timeout=self._SYNC_TIMEOUT,
                )
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.ConnectionError as exc:
            raise UserError(_("Cannot connect to BooKAI at %s") % base_url) from exc
        except requests.exceptions.Timeout as exc:
            raise UserError(_("BooKAI sync timed out.")) from exc
        except requests.exceptions.HTTPError as exc:
            raise UserError(
                _("BooKAI returned HTTP %s: %s")
                % (exc.response.status_code, exc.response.text[:500])
            ) from exc

        self._update_translation_status(data)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Success"),
                "message": _("Template '%s' synced to BooKAI.")
                % self.bookai_template_code,
                "type": "success",
                "sticky": False,
            },
        }

    def action_check_bookai_status(self):
        """Check Meta approval status for this template."""
        self.ensure_one()
        if not self.bookai_template_code:
            return
        base_url, headers = self._get_bookai_headers()
        url = (
            base_url.rstrip("/") + f"/api/v1/whatsapp/templates"
            f"/{self.bookai_template_code}/status"
        )
        try:
            resp = requests.get(url, headers=headers, timeout=self._SYNC_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.ConnectionError as exc:
            raise UserError(_("Cannot connect to BooKAI at %s") % base_url) from exc
        except requests.exceptions.HTTPError as exc:
            raise UserError(
                _("BooKAI returned HTTP %s: %s")
                % (exc.response.status_code, exc.response.text[:500])
            ) from exc

        self._update_translation_status(data)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Status Updated"),
                "message": _("Template '%s' status refreshed.")
                % self.bookai_template_code,
                "type": "info",
                "sticky": False,
            },
        }

    def _sync_translations_from_i18n(self):
        """Auto-create/update translations from body i18n.

        Creates one translation record per (language × WA account) for
        each WABA linked to the properties of this template.
        """
        self.ensure_one()
        Translation = self.env["bookai.whatsapp.translation"]
        active_langs = self.env["res.lang"].search([("active", "=", True)])

        # Collect WA accounts from associated properties
        wa_accounts = self.env["bookai.wa.account"]
        for prop in self.pms_property_ids:
            if prop.bookai_wa_phone_id and prop.bookai_wa_phone_id.wa_account_id:
                wa_accounts |= prop.bookai_wa_phone_id.wa_account_id
        # If template has no properties or none have WA, use empty account
        if not wa_accounts:
            wa_accounts = self.env["bookai.wa.account"]

        found_keys = set()  # (language, wa_account_id)
        for lang in active_langs:
            body = self.with_context(lang=lang.code).body
            if not body or not body.strip():
                continue
            short_code = lang.iso_code or lang.code.split("_")[0]

            accounts_to_process = wa_accounts or self.env["bookai.wa.account"]
            if not accounts_to_process:
                # No WA accounts: create translation without account
                key = (short_code, False)
                if key in found_keys:
                    continue
                found_keys.add(key)
                existing = Translation.search(
                    [
                        ("template_id", "=", self.id),
                        ("language", "=", short_code),
                        ("wa_account_id", "=", False),
                    ],
                    limit=1,
                )
                if not existing:
                    Translation.create(
                        {
                            "template_id": self.id,
                            "language": short_code,
                        }
                    )
            else:
                for wa_account in accounts_to_process:
                    key = (short_code, wa_account.id)
                    if key in found_keys:
                        continue
                    found_keys.add(key)
                    existing = Translation.search(
                        [
                            ("template_id", "=", self.id),
                            ("language", "=", short_code),
                            ("wa_account_id", "=", wa_account.id),
                        ],
                        limit=1,
                    )
                    if not existing:
                        Translation.create(
                            {
                                "template_id": self.id,
                                "language": short_code,
                                "wa_account_id": wa_account.id,
                            }
                        )

        # Deactivate stale translations
        found_langs = {k[0] for k in found_keys}
        stale = Translation.search(
            [
                ("template_id", "=", self.id),
                ("language", "not in", list(found_langs)),
                ("active", "=", True),
            ]
        )
        if stale:
            stale.write({"active": False})

    def _build_bookai_template_payload(self):
        self.ensure_one()
        param_keys = [p.key for p in self.bookai_param_ids.sorted("sequence")]
        translations = []
        for trans in self.bookai_translation_ids.filtered("active"):
            lang_code = trans.language
            odoo_lang = self._resolve_odoo_lang(lang_code)
            tmpl_lang = self.with_context(lang=odoo_lang)
            body_text = (tmpl_lang.body or "").strip()
            if not body_text:
                continue
            entry = {
                "language": lang_code,
                "body_text": body_text,
                "parameters": param_keys,
                "property_ids": (self.pms_property_ids.ids or []),
                "active": trans.active,
            }
            if trans.wa_account_id:
                entry["waba_id"] = trans.wa_account_id.waba_id
            if trans.meta_template_id:
                entry["meta_template_id"] = trans.meta_template_id
            # Body examples for Meta template validation
            body_examples = [
                p.example_value or p.key
                for p in self.bookai_param_ids.sorted("sequence")
            ]
            if body_examples:
                entry["body_example"] = body_examples
            header = (tmpl_lang.bookai_header_text or "").strip()
            if header:
                entry["header_text"] = header
            footer = (tmpl_lang.bookai_footer_text or "").strip()
            if footer:
                entry["footer_text"] = footer
            buttons = (tmpl_lang.bookai_button_texts or "").strip()
            if buttons:
                try:
                    entry["button_texts"] = json.loads(buttons)
                except (json.JSONDecodeError, TypeError):
                    pass
            translations.append(entry)
        return {
            "code": self.bookai_template_code,
            "category": self.bookai_category or "UTILITY",
            "translations": translations,
        }

    def _resolve_odoo_lang(self, short_code):
        """Resolve 'es' → 'es_ES', 'en' → 'en_US', etc."""
        lang = self.env["res.lang"].search(
            [
                ("active", "=", True),
                "|",
                ("code", "=like", f"{short_code}_%"),
                ("iso_code", "=", short_code),
            ],
            limit=1,
        )
        return lang.code if lang else short_code

    def _update_translation_status(self, data):
        """Update meta_status and meta_template_id from BookAI response.

        Supports two response formats:
        - New format: waba_entries[] inside each translation, each with
          waba_id, meta_status, meta_template_id.
        - Legacy format: meta_status / meta_template_id / waba_id directly
          at translation level.
        """
        self.ensure_one()
        for trans_data in data.get("translations", []):
            lang = trans_data.get("language")
            if not lang:
                continue
            waba_entries = trans_data.get("waba_entries", [])
            if waba_entries:
                for entry in waba_entries:
                    waba_id = entry.get("waba_id")
                    if not waba_id:
                        continue
                    trans = self.bookai_translation_ids.filtered(
                        lambda t, lc=lang, wid=waba_id: (
                            t.language == lc
                            and t.wa_account_id
                            and t.wa_account_id.waba_id == wid
                        )
                    )
                    if not trans:
                        continue
                    vals = {}
                    if entry.get("meta_status"):
                        vals["meta_status"] = entry["meta_status"]
                    if entry.get("meta_template_id"):
                        vals["meta_template_id"] = entry["meta_template_id"]
                    if vals:
                        trans.write(vals)
            else:
                # Legacy format: fields at translation level
                waba_id = trans_data.get("waba_id")
                trans = self.bookai_translation_ids.filtered(
                    lambda t, lang_code=lang, wid=waba_id: (
                        t.language == lang_code
                        and (
                            (not wid)
                            or (t.wa_account_id and t.wa_account_id.waba_id == wid)
                        )
                    )
                )
                if not trans:
                    continue
                vals = {}
                if trans_data.get("meta_status"):
                    vals["meta_status"] = trans_data["meta_status"]
                if trans_data.get("meta_template_id"):
                    vals["meta_template_id"] = trans_data["meta_template_id"]
                if vals:
                    trans.write(vals)

    @api.onchange("body", "bookai_param_ids")
    def _onchange_warn_multi_property(self):
        if len(self.pms_property_ids) > 1:
            return {
                "warning": {
                    "title": _("Multi-property template"),
                    "message": _(
                        "This template is linked to %d properties. "
                        "Changes will affect all of them when synced."
                    )
                    % len(self.pms_property_ids),
                }
            }

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
