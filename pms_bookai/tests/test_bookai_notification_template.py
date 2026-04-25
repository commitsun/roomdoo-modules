import json
from unittest.mock import MagicMock, patch

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from .common import TestBookaiCommon


@tagged("post_install", "-at_install")
class TestBookaiNotificationTemplate(TestBookaiCommon):
    def test_channel_bookai_whatsapp_enabled_true(self):
        self.assertTrue(self.bookai_template.channel_bookai_whatsapp_enabled)

    def test_channel_bookai_whatsapp_enabled_false(self):
        tmpl = self.env["pms.notification.template"].create(
            {
                "name": "No Code",
                "code": "no_code",
                "model_id": self.folio_model.id,
            }
        )
        self.assertFalse(tmpl.channel_bookai_whatsapp_enabled)

    def test_body_keys_constraint_unknown_key(self):
        with self.assertRaises(ValidationError):
            self.bookai_template.write({"body": "Hello {{ unknown_param }}"})

    def test_body_keys_constraint_valid(self):
        self.bookai_template.write({"body": "Hello {{ guest_name }}"})
        self.assertIn("guest_name", self.bookai_template.body)

    def test_required_fields_constraint_active_rule(self):
        tmpl = self.env["pms.notification.template"].create(
            {
                "name": "Missing fields",
                "code": "missing_fields",
                "model_id": self.folio_model.id,
                "bookai_template_code": "missing_v1",
            }
        )
        self.env["pms.property.notification.rule"].create(
            {
                "name": "Active rule",
                "template_id": tmpl.id,
                "target_model_id": self.folio_model.id,
                "rule_type": "event",
                "event_type": "on_create",
                "event_domain": "[]",
                "channel": "bookai_whatsapp",
                "send_immediately": False,
            }
        )
        with self.assertRaises(ValidationError):
            tmpl.write({"bookai_recipient_phone_tmpl": ""})

    def test_required_fields_constraint_no_rule(self):
        tmpl = self.env["pms.notification.template"].create(
            {
                "name": "No rule",
                "code": "no_rule",
                "model_id": self.folio_model.id,
                "bookai_template_code": "norule_v1",
            }
        )
        tmpl.write({"bookai_recipient_phone_tmpl": ""})
        self.assertFalse(tmpl.bookai_recipient_phone_tmpl)

    def test_render_inline_empty_returns_empty(self):
        result = self.bookai_template._bookai_render_inline("", self.folio)
        self.assertEqual(result, "")

    def test_render_body_with_params(self):
        body, params, lang, tz = self.bookai_template._bookai_render_body(self.folio)
        self.assertIn("Test Guest", body)

    def test_to_whatsapp_text_strips_html(self):
        result = self.bookai_template._bookai_to_whatsapp_text("<p>Hello World</p>")
        self.assertEqual(result, "Hello World")

    def test_to_whatsapp_text_br_to_newline(self):
        result = self.bookai_template._bookai_to_whatsapp_text("Line1<br/>Line2")
        self.assertIn("\n", result)

    def test_to_whatsapp_text_collapses_newlines(self):
        result = self.bookai_template._bookai_to_whatsapp_text("A\n\n\n\n\nB")
        self.assertNotIn("\n\n\n", result)

    def test_sync_translations_creates_from_langs(self):
        self.bookai_template._sync_translations_from_i18n()
        translations = self.bookai_template.bookai_translation_ids
        self.assertTrue(translations)

    def test_sync_translations_deactivates_stale(self):
        trans = self.env["bookai.whatsapp.translation"].create(
            {
                "template_id": self.bookai_template.id,
                "language": "zz",
            }
        )
        self.bookai_template._sync_translations_from_i18n()
        trans.invalidate_recordset()
        self.assertFalse(trans.active)

    def test_build_payload_includes_meta_template_id(self):
        self.bookai_template._sync_translations_from_i18n()
        trans = self.bookai_template.bookai_translation_ids[:1]
        trans.write({"meta_template_id": "meta-123"})
        payload = self.bookai_template._build_bookai_template_payload()
        found = [
            t
            for t in payload["translations"]
            if t.get("meta_template_id") == "meta-123"
        ]
        self.assertTrue(found)

    def test_build_payload_skips_empty_meta_template_id(self):
        self.bookai_template._sync_translations_from_i18n()
        payload = self.bookai_template._build_bookai_template_payload()
        for t in payload["translations"]:
            self.assertNotIn("meta_template_id", t)

    def test_build_payload_skips_empty_body(self):
        # Template without body → translation should be skipped
        tmpl_empty = self.env["pms.notification.template"].create(
            {
                "name": "Empty body tmpl",
                "code": "empty_body",
                "model_id": self.folio_model.id,
                "bookai_template_code": "empty_body_v1",
            }
        )
        self.env["bookai.whatsapp.translation"].create(
            {
                "template_id": tmpl_empty.id,
                "language": "es",
            }
        )
        payload = tmpl_empty._build_bookai_template_payload()
        self.assertEqual(len(payload["translations"]), 0)

    def test_build_payload_includes_optional_fields(self):
        self.bookai_template.write(
            {
                "bookai_header_text": "Header",
                "bookai_footer_text": "Footer",
                "bookai_button_texts": json.dumps([{"type": "URL", "text": "Go"}]),
            }
        )
        self.bookai_template._sync_translations_from_i18n()
        payload = self.bookai_template._build_bookai_template_payload()
        if payload["translations"]:
            t = payload["translations"][0]
            self.assertEqual(t.get("header_text"), "Header")
            self.assertEqual(t.get("footer_text"), "Footer")
            self.assertTrue(t.get("button_texts"))

    def test_update_translation_status(self):
        self.bookai_template._sync_translations_from_i18n()
        trans = self.bookai_template.bookai_translation_ids[:1]
        lang = trans.language
        self.bookai_template._update_translation_status(
            {
                "translations": [
                    {
                        "language": lang,
                        "meta_status": "approved",
                        "meta_template_id": "meta-456",
                    }
                ]
            }
        )
        trans.invalidate_recordset()
        self.assertEqual(trans.meta_status, "approved")
        self.assertEqual(trans.meta_template_id, "meta-456")

    def test_resolve_odoo_lang(self):
        es_lang = self.env["res.lang"].search(
            [("code", "=like", "es_%"), ("active", "=", True)],
            limit=1,
        )
        if es_lang:
            result = self.bookai_template._resolve_odoo_lang("es")
            self.assertEqual(result, es_lang.code)

    def test_sync_to_bookai_409_patches(self):
        mock_resp_409 = MagicMock()
        mock_resp_409.status_code = 409
        mock_resp_409.raise_for_status = MagicMock()

        mock_resp_ok = MagicMock()
        mock_resp_ok.status_code = 200
        mock_resp_ok.json.return_value = {"translations": []}
        mock_resp_ok.raise_for_status = MagicMock()

        with patch(
            "odoo.addons.pms_bookai.models." "pms_notification_template.requests.post",
            return_value=mock_resp_409,
        ), patch(
            "odoo.addons.pms_bookai.models." "pms_notification_template.requests.patch",
            return_value=mock_resp_ok,
        ) as mock_patch:
            self.bookai_template.action_sync_to_bookai()
        self.assertTrue(mock_patch.called)
