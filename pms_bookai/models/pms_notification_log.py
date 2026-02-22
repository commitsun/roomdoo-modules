import json
import logging
import re
from datetime import date, datetime

import phonenumbers
import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from odoo.addons.phone_validation.tools import phone_validation

_logger = logging.getLogger(__name__)

# Keep the same keys that were already in use.
ICP_ENDPOINT_KEY = "pms_bookai.api_endpoint"
ICP_TOKEN_KEY = "pms_bookai.api_token"
ICP_VERIFY_SSL_KEY = "pms_bookai.verify_ssl"
ICP_TIMEOUT_KEY = "pms_bookai.timeout"


class PmsNotificationLog(models.Model):
    _inherit = "pms.notification.log"

    channel = fields.Selection(
        selection_add=[("bookai_whatsapp", "BookAI WhatsApp")],
        ondelete={"bookai_whatsapp": "set default"},
    )

    # Fields resolved at log creation (visible in UI before sending).
    whatsapp_phone = fields.Char(string="WhatsApp Phone", index=True)
    whatsapp_country = fields.Char(string="WhatsApp Country Code", size=2)
    whatsapp_recipient_name = fields.Char(string="WhatsApp Recipient Name")
    whatsapp_language = fields.Char(string="WhatsApp Language", size=16)

    bookai_origin_folio_id = fields.Many2one("pms.folio", string="Origin Folio")

    whatsapp_template_parameters = fields.Text(
        string="WhatsApp Template Parameters (JSON)",
        help="Resolved JSON parameters sent to BookAI.",
    )
    whatsapp_body_preview = fields.Text(
        string="WhatsApp Body Preview",
        help=(
            "Rendered WhatsApp body preview for this log "
            "(using resolved parameters and language)."
        ),
    )
    # Debug: last HTTP interaction with BookAI (stored for troubleshooting)
    bookai_last_http_status = fields.Integer(
        string="BookAI HTTP Status",
        help="HTTP status code returned by BookAI in the last send attempt.",
    )
    bookai_last_request_payload = fields.Text(
        string="BookAI Request Payload (JSON)",
        help="Exact JSON payload sent to BookAI in the last send attempt.",
    )
    bookai_last_request_headers = fields.Text(
        string="BookAI Request Headers (JSON)",
        help=(
            "Headers sent to BookAI in the last send attempt " "(Authorization masked)."
        ),
    )
    bookai_last_response_body = fields.Text(
        string="BookAI Response Body",
        help=(
            "Raw response body returned by BookAI in the last send attempt "
            "(JSON or text)."
        ),
    )

    # -------------------------------------------------------------------------
    # CREATE: one log per partner + precompute BookAI fields.
    # -------------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get("bookai_no_split"):
            logs = super().create(vals_list)
            logs._bookai_prepare_logs_on_create()
            return logs

        expanded = []
        for vals in vals_list:
            if vals.get("channel") != "bookai_whatsapp":
                expanded.append(vals)
                continue

            partner_ids = self._extract_m2m_ids(vals.get("recipient_partner_ids"))

            # 0 o 1 partner -> no split
            if len(partner_ids) <= 1:
                expanded.append(vals)
                continue

            # split N logs
            for pid in partner_ids:
                v = dict(vals)
                v["recipient_mode"] = "partners"
                v["recipient_emails"] = False
                v["recipient_partner_ids"] = [(6, 0, [pid])]
                expanded.append(v)

        logs = super(
            PmsNotificationLog, self.with_context(bookai_no_split=True)
        ).create(expanded)
        logs._bookai_prepare_logs_on_create()
        return logs

    def _extract_m2m_ids(self, commands):
        if not commands:
            return []
        if isinstance(commands, list) and commands and isinstance(commands[0], int):
            return list(dict.fromkeys(commands))
        ids = set()
        if isinstance(commands, list):
            for cmd in commands:
                if not isinstance(cmd, list | tuple) or not cmd:
                    continue
                op = cmd[0]
                if op == 6 and len(cmd) >= 3:
                    ids |= set(cmd[2] or [])
                elif op == 4 and len(cmd) >= 2:
                    ids.add(cmd[1])
        return list(ids)

    def _bookai_prepare_logs_on_create(self):
        logs = self.filtered(lambda log: log.channel == "bookai_whatsapp")
        if logs:
            logs._bookai_prepare_payload_fields()
        return True

    # -------------------------------------------------------------------------
    # Config helpers
    # -------------------------------------------------------------------------
    def _bookai_get_api_endpoint(self):
        return (
            self.env["ir.config_parameter"].sudo().get_param(ICP_ENDPOINT_KEY) or ""
        ).strip()

    def _bookai_get_api_token(self):
        return (
            self.env["ir.config_parameter"].sudo().get_param(ICP_TOKEN_KEY) or ""
        ).strip()

    def _bookai_get_verify_ssl(self):
        val = (
            self.env["ir.config_parameter"].sudo().get_param(ICP_VERIFY_SSL_KEY) or "0"
        ).strip()
        return val not in ("0", "false", "False", "")

    def _bookai_get_timeout(self):
        val = (
            self.env["ir.config_parameter"].sudo().get_param(ICP_TIMEOUT_KEY) or "30"
        ).strip()
        try:
            return int(val)
        except Exception:
            return 30

    def _bookai_get_instance_url(self):
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        return base_url or "https://odoo-instance"

    # -------------------------------------------------------------------------
    # Property mode guard (does not break flows: create marks as skipped).
    # -------------------------------------------------------------------------
    def _bookai_check_property_mode(self):
        self.ensure_one()
        prop = self.property_id
        if not prop or "bookai_mode" not in prop._fields:
            return True, False, False

        if prop.bookai_mode in (False, "disabled"):
            return False, "skipped", _(
                "BookAI disabled for property '%s'."
            ) % prop.display_name

        if prop.bookai_mode == "manual" and self.rule_id:
            return False, "skipped", _(
                "BookAI is in MANUAL mode for property '%s'; "
                "rule sending is disabled."
            ) % prop.display_name

        return True, False, False

    # -------------------------------------------------------------------------
    # PRECOMPUTE during log creation.
    # -------------------------------------------------------------------------
    def _bookai_prepare_payload_fields(self):
        """
        Does not swallow exceptions: if something fails, create/write should fail.
        The caller handles persistence through the regular error path.
        """
        for log in self:
            if log.state in ("sent", "cancelled"):
                continue

            try:
                ok, state, msg = log._bookai_check_property_mode()
                if not ok:
                    log.write({"state": state, "error_message": msg})
                    continue

                endpoint = log._bookai_get_api_endpoint()
                token = log._bookai_get_api_token()
                if not endpoint or not token:
                    raise ValidationError(
                        _("BookAI API endpoint/token is not configured.")
                    )

                template = log.template_id
                if not template or not getattr(template, "bookai_template_code", False):
                    raise ValidationError(
                        _("Missing BookAI template code on notification template.")
                    )

                record = log._get_record_to_notify()

                # Partner override: after split this should be 0 or 1.
                partners = log.recipient_partner_ids
                partner = partners[0] if len(partners) == 1 else False

                (
                    phone,
                    name,
                    template_lang,
                    fallback_country,
                    render_lang,
                ) = log._bookai_resolve_recipient(
                    template=template,
                    record=record,
                    partner=partner,
                )

                phone = log._bookai_normalize_phone(
                    phone, default_country=fallback_country
                )
                country = log._bookai_guess_country_from_phone(
                    phone,
                    fallback_country=fallback_country,
                )
                if not country:
                    raise ValidationError(
                        _("Cannot determine recipient country for phone '%s'.") % phone
                    )

                folio = log._bookai_resolve_origin_folio(
                    template=template, record=record
                )

                tz = (log.property_id.tz or "").strip() or (log.env.user.tz or "UTC")
                params = template._bookai_build_parameters(
                    record, lang=render_lang, tz=tz
                )
                body_preview = template._render_body_with_params(
                    record=record,
                    params=params,
                    lang=render_lang,
                    tz=tz,
                )

                log.write(
                    {
                        "whatsapp_phone": phone,
                        "whatsapp_country": country,
                        "whatsapp_recipient_name": name or "",
                        "whatsapp_language": template_lang or "",
                        "bookai_origin_folio_id": folio.id,
                        "whatsapp_template_parameters": json.dumps(
                            params, ensure_ascii=False
                        ),
                        "whatsapp_body_preview": body_preview or "",
                        "error_message": False,
                    }
                )

            except Exception as err:
                _logger.exception("BookAI prepare failed for log %s", log.id)
                log.write({"state": "error", "error_message": str(err)})
                raise err

        return True

    def _bookai_resolve_recipient(self, template, record, partner=False):
        """
        (phone, display_name, template_lang, fallback_country_iso2, render_lang)

        - If log has partner (manual wizard / split): use partner
        - Otherwise: render from template bookai_*_tmpl against origin record
        """
        self.ensure_one()
        user_lang = self._bookai_get_active_lang_code()
        if partner:
            phone = (partner.mobile or partner.phone or "").strip()
            display_name = (partner.name or partner.display_name or "").strip()

            lang = partner.lang
            if not lang and "lang" in record._fields:
                lang = record.lang
            if not lang:
                lang = user_lang

            fallback_country = self._bookai_get_fallback_country_iso2(
                record=record,
                partner=partner,
            )
            template_lang, render_lang = self._bookai_normalize_lang_codes(
                lang,
                default_lang=user_lang,
            )
            return phone, display_name, template_lang, fallback_country, render_lang

        phone = template._bookai_render_inline(
            template.bookai_recipient_phone_tmpl, record
        ).strip()
        if not phone:
            raise ValidationError(_("BookAI recipient phone is empty after rendering."))

        display_name = template._bookai_render_inline(
            template.bookai_recipient_name_tmpl, record
        ).strip()
        lang = (
            template._bookai_render_inline(template.bookai_language_tmpl, record)
            or user_lang
        )

        fallback_country = self._bookai_get_fallback_country_iso2(record=record)
        template_lang, render_lang = self._bookai_normalize_lang_codes(
            lang,
            default_lang=user_lang,
        )
        return phone, display_name, template_lang, fallback_country, render_lang

    def _bookai_get_active_lang(self, *candidates):
        self.ensure_one()
        Lang = self.env["res.lang"]

        for candidate in candidates:
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
                return lang_rec

        return Lang.search([("active", "=", True)], limit=1)

    def _bookai_get_active_lang_code(self):
        self.ensure_one()
        lang_rec = self._bookai_get_active_lang(
            self.env.context.get("lang"),
            self.env.user.lang,
        )
        return lang_rec.code or ""

    def _bookai_normalize_lang_codes(self, lang, default_lang=None):
        """
        Return (template_lang_iso2, render_lang_code).
        - template_lang_iso2: language for BookAI payload.
        - render_lang_code: Odoo language code used for rendering.

        Resolution uses direct matches on res.lang.code / res.lang.iso_code.
        Final fallback is any active language in the instance.
        """
        self.ensure_one()

        lang_rec = self._bookai_get_active_lang(
            lang,
            default_lang,
            self.env.context.get("lang"),
            self.env.user.lang,
        )

        if not lang_rec:
            return "", ""

        render_lang = lang_rec.code or ""
        template_lang = lang_rec.iso_code or render_lang
        return template_lang, render_lang

    def _bookai_get_fallback_country_iso2(self, record=False, partner=False):
        """
        Return best-effort ISO2 fallback country for phone parsing/normalization.
        Priority:
        1) explicit recipient partner
        2) record.partner_id
        3) record.pms_property_id / record.property_id
        4) log.property_id
        5) current company
        """
        self.ensure_one()

        candidates = []

        if partner and partner.country_id:
            candidates.append(partner.country_id)

        if record and "partner_id" in record._fields and record.partner_id.country_id:
            candidates.append(record.partner_id.country_id)

        if (
            record
            and "pms_property_id" in record._fields
            and record.pms_property_id.country_id
        ):
            candidates.append(record.pms_property_id.country_id)

        if record and "property_id" in record._fields and record.property_id.country_id:
            candidates.append(record.property_id.country_id)

        if self.property_id and self.property_id.country_id:
            candidates.append(self.property_id.country_id)

        if self.env.company.country_id:
            candidates.append(self.env.company.country_id)

        for country in candidates:
            code = (country.code or "").upper()
            if code:
                return code
        return ""

    def _bookai_get_country_context(self, country_iso2):
        self.ensure_one()
        iso2 = (country_iso2 or "").upper()
        if not iso2:
            return None, None

        country = self.env["res.country"].search([("code", "=", iso2)], limit=1)
        if not country:
            return iso2, None
        return (country.code or iso2).upper(), (country.phone_code or None)

    def _bookai_parse_phone_with_phonenumbers(self, phone, default_country=None):
        """
        Strict parse using python-phonenumbers.
        Returns parsed phone object or False.
        """
        self.ensure_one()

        try:
            parsed = phonenumbers.parse(phone, region=(default_country or None))
        except phonenumbers.phonenumberutil.NumberParseException:
            return False

        if not phonenumbers.is_possible_number(parsed):
            return False
        if not phonenumbers.is_valid_number(parsed):
            return False
        return parsed

    def _bookai_is_e164(self, phone):
        self.ensure_one()
        return bool(re.fullmatch(r"\+[1-9]\d{4,14}", (phone or "").strip()))

    def _bookai_normalize_phone(self, phone, default_country=None):
        """
        Normalize recipient phone to E164 using Odoo phone_validation.
        Falls back to phonenumbers direct parse.
        Last resort keeps legacy cleanup and country.phone_code prefixing.
        """
        self.ensure_one()

        p = (phone or "").strip()
        if not p:
            raise ValidationError(_("Recipient phone is missing."))

        country_code, country_phone_code = self._bookai_get_country_context(
            default_country
        )

        sanitize_res = phone_validation.phone_sanitize_numbers(
            [p],
            country_code,
            country_phone_code,
            force_format="E164",
        )
        sanitized = sanitize_res.get(p, {}).get("sanitized")
        if sanitized and self._bookai_is_e164(sanitized):
            return sanitized

        parsed = self._bookai_parse_phone_with_phonenumbers(p, default_country)
        if parsed:
            return phonenumbers.format_number(
                parsed, phonenumbers.PhoneNumberFormat.E164
            )

        # Last-resort normalization for already-digit numbers that still fail parse.
        p = re.sub(r"[ \t\r\n\-\(\)\.]", "", p)
        if p.startswith("00"):
            p = "+" + p[2:]

        if not p.startswith("+") and country_phone_code:
            p = "+" + str(country_phone_code) + p.lstrip("0")

        if p.startswith("+"):
            sanitize_res = phone_validation.phone_sanitize_numbers(
                [p],
                country_code,
                country_phone_code,
                force_format="E164",
            )
            sanitized = sanitize_res.get(p, {}).get("sanitized")
            if sanitized and self._bookai_is_e164(sanitized):
                return sanitized

        if not self._bookai_is_e164(p):
            raise ValidationError(
                _("Phone must be in international format (e.g. +346...). Got: %s") % p
            )

        return p

    def _bookai_guess_country_from_phone(self, phone, fallback_country=None):
        self.ensure_one()
        p = (phone or "").strip()
        if not p:
            return (fallback_country or "").upper()

        default_country = (fallback_country or "").upper() or None
        country = ""

        try:
            parsed = phone_validation.phone_parse(p, default_country)
        except UserError:
            parsed = False

        if parsed:
            country = phonenumbers.phonenumberutil.region_code_for_number(parsed) or ""

        if not country:
            parsed = self._bookai_parse_phone_with_phonenumbers(p, default_country)
            if parsed:
                country = (
                    phonenumbers.phonenumberutil.region_code_for_number(parsed) or ""
                )

        if not country and default_country:
            country = default_country

        return (country or "").upper()

    def _bookai_resolve_origin_folio(self, template, record):
        """
        Resolves folio via template.bookai_origin_folio_id_tmpl:
        - folio: {{ object.id }}
        - related: {{ object.folio_id.id }}
        """
        self.ensure_one()

        src = (template.bookai_origin_folio_id_tmpl or "").strip()
        if not src:
            raise ValidationError(_("Missing Origin Folio ID template expression."))

        rendered = template._bookai_render_inline(src, record).strip()
        if not rendered:
            raise ValidationError(_("Origin Folio ID template rendered empty."))

        try:
            folio_id = int(float(rendered))
        except Exception as err:
            raise ValidationError(
                _("Origin Folio ID must be an integer. Got: %s") % rendered
            ) from err

        folio = self.env["pms.folio"].browse(folio_id).exists()
        if not folio:
            raise ValidationError(_("Origin folio not found: id=%s") % folio_id)
        return folio

    # -------------------------------------------------------------------------
    # Dispatcher
    # -------------------------------------------------------------------------
    def action_send_by_channel(self):
        bookai_logs = self.filtered(lambda log: log.channel == "bookai_whatsapp")
        other_logs = self - bookai_logs

        if bookai_logs:
            bookai_logs.action_send_bookai_whatsapp()

        if other_logs:
            return super(PmsNotificationLog, other_logs).action_send_by_channel()

        return True

    # -------------------------------------------------------------------------
    # Sender
    # -------------------------------------------------------------------------
    def action_send_bookai_whatsapp(self):
        for log in self:
            if log.state in ("sent", "cancelled", "skipped", "error"):
                continue

            try:
                ok, state, msg = log._bookai_check_property_mode()
                if not ok:
                    log.write({"state": state, "error_message": msg})
                    continue

                # Fallback: if legacy logs miss payload data, prepare it now.
                if (
                    not log.whatsapp_phone
                    or not log.whatsapp_country
                    or not log.bookai_origin_folio_id
                ):
                    log._bookai_prepare_payload_fields()

                if not log.whatsapp_phone or not log.whatsapp_country:
                    raise ValidationError(
                        _("Missing recipient phone/country on the log.")
                    )

                template = log.template_id
                params = (
                    json.loads(log.whatsapp_template_parameters)
                    if (log.whatsapp_template_parameters or "").strip()
                    else {}
                )

                payload = log._build_bookai_payload(
                    phone=log.whatsapp_phone,
                    country=log.whatsapp_country,
                    template_code=template.bookai_template_code,
                    template_language=log._bookai_normalize_lang_codes(
                        log.whatsapp_language
                    )[0],
                    display_name=log.whatsapp_recipient_name or "",
                    parameters=params,
                )

                response = log._send_bookai_request(payload)

                log.write(
                    {
                        "state": "sent",
                        "sent_date": fields.Datetime.now(),
                        "error_message": False,
                        "external_reference": response.get("message_id", "")
                        or response.get("id", ""),
                        "external_status": response.get("status", ""),
                        "external_payload": json.dumps(response, ensure_ascii=False),
                    }
                )

            except Exception as e:
                _logger.exception("BookAI WhatsApp failed for log %s", log.id)
                log.write({"state": "error", "error_message": str(e)})

        return True

    # -------------------------------------------------------------------------
    # Payload + Request
    # -------------------------------------------------------------------------
    def _to_ymd(self, value):
        if not value:
            return ""
        if isinstance(value, datetime):
            return fields.Date.to_string(value.date())
        if isinstance(value, date):
            return fields.Date.to_string(value)
        return str(value)[:10]

    def _build_bookai_payload(
        self, phone, country, template_code, template_language, display_name, parameters
    ):
        self.ensure_one()

        prop = self.property_id
        hotel_info = (
            prop.get_bookai_hotel_info()
            if prop and hasattr(prop, "get_bookai_hotel_info")
            else {
                "id": prop.id if prop else 0,
                "external_code": "",
                "name": "",
            }
        )

        instance_url = self._bookai_get_instance_url()
        db_name = self.env.cr.dbname
        create_ts = self.create_date.timestamp() if self.create_date else 0

        source = {
            "instance_url": instance_url,
            "db": db_name,
            "instance_id": instance_url,
            "hotel": hotel_info,
        }

        folio = self.bookai_origin_folio_id
        if folio:
            source["origin_folio"] = {
                "id": folio.id,
                "code": folio.name or "",
                "min_checkin": self._to_ymd(getattr(folio, "first_checkin", False)),
                "max_checkout": self._to_ymd(getattr(folio, "last_checkout", False)),
            }

        return {
            "source": source,
            "recipient": {
                "phone": phone,
                "country": country,
                "display_name": display_name or "",
            },
            "template": {
                "code": template_code,
                "language": template_language,
                "parameters": parameters or {},
            },
            "meta": {
                "trigger": self.template_id.code or "manual",
                "idempotency_key": (f"notification-{self.id}-{phone}-{create_ts}"),
            },
        }

    def _send_bookai_request(self, payload):
        self.ensure_one()

        base_endpoint = self._bookai_get_api_endpoint().rstrip("/")
        url = f"{base_endpoint}/api/v1/whatsapp/send-template"
        headers = {
            "Authorization": f"Bearer {self._bookai_get_api_token()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        # Persist exact request payload for troubleshooting from the UI.
        # (Headers are stored with the Authorization token masked.)
        headers_masked = dict(headers)
        if headers_masked.get("Authorization"):
            headers_masked["Authorization"] = "Bearer ***"

        self.write(
            {
                "bookai_last_request_payload": json.dumps(
                    payload, indent=2, ensure_ascii=False
                ),
                "bookai_last_request_headers": json.dumps(
                    headers_masked, indent=2, ensure_ascii=False
                ),
                "bookai_last_http_status": 0,
                "bookai_last_response_body": False,
            }
        )

        _logger.info("Sending BookAI WhatsApp request to %s", url)
        _logger.info(
            "BookAI payload: %s", json.dumps(payload, indent=2, ensure_ascii=False)
        )

        try:
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=self._bookai_get_timeout(),
                verify=self._bookai_get_verify_ssl(),
            )

            # Store response ALWAYS (even on non-2xx)
            self.write(
                {
                    "bookai_last_http_status": response.status_code,
                    "bookai_last_response_body": response.text or "",
                }
            )

            response.raise_for_status()
            return response.json() if response.content else {}

        except requests.exceptions.HTTPError as e:
            # Keep the raw body if present (already stored above, but ensure it exists)
            try:
                status = e.response.status_code if e.response else "N/A"
                detail = (
                    e.response.json()
                    if e.response and e.response.content
                    else (e.response.text if e.response else str(e))
                )
            except Exception:
                status = e.response.status_code if e.response else "N/A"
                detail = str(e)

            raise UserError(
                _("BookAI API request failed with status %s: %s") % (status, detail)
            ) from e

        except requests.exceptions.RequestException as e:
            # Network errors / timeouts: persist error for UI troubleshooting
            status = getattr(getattr(e, "response", None), "status_code", 0) or 0
            body = ""
            try:
                body = getattr(getattr(e, "response", None), "text", "") or ""
            except Exception:
                body = ""

            self.write(
                {
                    "bookai_last_http_status": status,
                    "bookai_last_response_body": body or str(e),
                }
            )

            raise UserError(_("BookAI API connection error: %s") % str(e)) from e
