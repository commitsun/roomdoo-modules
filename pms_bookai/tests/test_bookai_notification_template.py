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

    def _in_meta(self, *languages):
        for language in languages:
            self.env["bookai.whatsapp.translation"].create(
                {
                    "template_id": self.bookai_template.id,
                    "language": language,
                    "meta_template_id": f"meta-{language}",
                }
            )

    def test_a_code_in_meta_does_not_sync_a_language_it_has_no_text_in(self):
        self.env["res.lang"]._activate_lang("es_ES")
        self.env["res.lang"]._activate_lang("gl_ES")
        self._in_meta("en", "es")  # es is in Meta although the body has no es text
        stale_gl = self.env["bookai.whatsapp.translation"].create(
            {"template_id": self.bookai_template.id, "language": "gl"}
        )
        self.assertEqual(
            sorted(self.bookai_template.bookai_sync_languages()), ["en_US", "es_ES"]
        )
        self.bookai_template._sync_translations_from_i18n()
        stale_gl.invalidate_recordset()
        self.assertFalse(stale_gl.active)
        payload = self.bookai_template._build_bookai_template_payload()
        self.assertEqual(
            sorted({t["language"] for t in payload["translations"]}), ["en", "es"]
        )

    def test_a_code_in_meta_syncs_a_language_with_its_own_text(self):
        self.env["res.lang"]._activate_lang("gl_ES")
        self._in_meta("en")
        self.bookai_template.with_context(lang="gl_ES").write(
            {"body": "Ola {{ guest_name }}"}
        )
        self.assertIn("gl_ES", self.bookai_template.bookai_sync_languages())

    def test_a_code_not_in_meta_yet_syncs_every_active_language(self):
        self.env["res.lang"]._activate_lang("gl_ES")
        self.assertIn("gl_ES", self.bookai_template.bookai_sync_languages())

    def test_another_hotels_template_puts_the_code_in_meta(self):
        self.env["res.lang"]._activate_lang("gl_ES")
        self._in_meta("en")
        _prop, other = self._hotel_template("Third Hotel")
        self.assertNotIn("gl_ES", other.bookai_sync_languages())

    def test_sync_languages_of_no_template_is_empty(self):
        self.assertEqual(
            self.env["pms.notification.template"].bookai_sync_languages(), []
        )

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

    def _hotel_template(self, name, code=None):
        """A per-hotel copy of the common template: same BookAI code."""
        prop = self.env["pms.property"].create(
            {
                "name": name,
                "company_id": self.company1.id,
                "default_pricelist_id": self.pricelist1.id,
            }
        )
        tmpl = self.env["pms.notification.template"].create(
            {
                "name": f"BookAI template {name}",
                "code": f"test_bookai_{name.lower().replace(' ', '_')}",
                "model_id": self.folio_model.id,
                "bookai_template_code": code
                or self.bookai_template.bookai_template_code,
                "pms_property_ids": [(6, 0, [prop.id])],
            }
        )
        self.env["pms.notification.template.bookai.param"].create(
            {
                "template_id": tmpl.id,
                "key": "guest_name",
                "value_type": "literal",
                "value_literal": name,
            }
        )
        tmpl.write({"body": "Hello {{ guest_name }}"})
        return prop, tmpl

    def test_build_payload_binds_every_hotel_sharing_the_code(self):
        self.bookai_template.pms_property_ids = [(6, 0, [self.pms_property1.id])]
        other, other_tmpl = self._hotel_template("Second Hotel")
        other_tmpl._sync_translations_from_i18n()
        payload = other_tmpl._build_bookai_template_payload()
        self.assertTrue(payload["translations"])
        for t in payload["translations"]:
            self.assertEqual(
                t["property_ids"], sorted([self.pms_property1.id, other.id])
            )
        self.assertEqual(
            self.bookai_template.bookai_bound_property_ids(),
            sorted([self.pms_property1.id, other.id]),
        )

    def test_build_payload_ignores_archived_and_other_codes(self):
        self.bookai_template.pms_property_ids = [(6, 0, [self.pms_property1.id])]
        _archived, archived_tmpl = self._hotel_template("Archived Hotel")
        archived_tmpl.active = False
        self._hotel_template("Other Code", code="another_v1")
        self.bookai_template._sync_translations_from_i18n()
        payload = self.bookai_template._build_bookai_template_payload()
        self.assertTrue(payload["translations"])
        for t in payload["translations"]:
            self.assertEqual(t["property_ids"], [self.pms_property1.id])

    def test_build_payload_of_a_lone_global_template_binds_nothing(self):
        self.bookai_template.pms_property_ids = [(5, 0, 0)]
        self.bookai_template._sync_translations_from_i18n()
        payload = self.bookai_template._build_bookai_template_payload()
        for t in payload["translations"]:
            self.assertEqual(t["property_ids"], [])

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

    def test_update_translation_status_legacy(self):
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

    def test_update_translation_status_waba_entries(self):
        wa_account = self.env["bookai.wa.account"].create(
            {"name": "Test WABA", "waba_id": "999888777"}
        )
        trans = self.env["bookai.whatsapp.translation"].create(
            {
                "template_id": self.bookai_template.id,
                "language": "es",
                "wa_account_id": wa_account.id,
            }
        )
        self.bookai_template._update_translation_status(
            {
                "translations": [
                    {
                        "language": "es",
                        "waba_entries": [
                            {
                                "waba_id": "999888777",
                                "meta_status": "approved",
                                "meta_template_id": "meta-789",
                            }
                        ],
                    }
                ]
            }
        )
        trans.invalidate_recordset()
        self.assertEqual(trans.meta_status, "approved")
        self.assertEqual(trans.meta_template_id, "meta-789")

    def test_update_translation_status_waba_entries_unknown_waba(self):
        """waba_entries with unknown waba_id should not crash."""
        self.bookai_template._sync_translations_from_i18n()
        trans = self.bookai_template.bookai_translation_ids[:1]
        old_status = trans.meta_status
        self.bookai_template._update_translation_status(
            {
                "translations": [
                    {
                        "language": trans.language,
                        "waba_entries": [
                            {
                                "waba_id": "unknown_waba",
                                "meta_status": "approved",
                                "meta_template_id": "meta-000",
                            }
                        ],
                    }
                ]
            }
        )
        trans.invalidate_recordset()
        self.assertEqual(trans.meta_status, old_status)

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
