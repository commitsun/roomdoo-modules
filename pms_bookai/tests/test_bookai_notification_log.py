from datetime import date, datetime
from unittest.mock import MagicMock, patch

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import TestBookaiCommon


@tagged("post_install", "-at_install")
class TestBookaiNotificationLog(TestBookaiCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pms_property1.write({"bookai_mode": "ai"})
        cls.partner1 = cls.env["res.partner"].create(
            {
                "name": "Partner 1",
                "mobile": "+34600000001",
                "country_id": cls.env.ref("base.es").id,
            }
        )
        cls.partner2 = cls.env["res.partner"].create(
            {
                "name": "Partner 2",
                "mobile": "+34600000002",
                "country_id": cls.env.ref("base.es").id,
            }
        )
        cls.partner3 = cls.env["res.partner"].create(
            {
                "name": "Partner 3",
                "mobile": "+34600000003",
                "country_id": cls.env.ref("base.es").id,
            }
        )

    def _create_log(self, **kwargs):
        vals = {
            "name": "Test Log",
            "template_id": self.bookai_template.id,
            "channel": "bookai_whatsapp",
            "origin_model": "pms.folio",
            "origin_res_id": self.folio.id,
            "property_id": self.pms_property1.id,
            "recipient_mode": "template",
        }
        vals.update(kwargs)
        return self.env["pms.notification.log"].create(vals)

    # ---------------------------------------------------------------
    # Log splitting
    # ---------------------------------------------------------------
    def test_create_splits_multiple_partners(self):
        logs = self.env["pms.notification.log"].create(
            {
                "name": "Split Log",
                "template_id": self.bookai_template.id,
                "channel": "bookai_whatsapp",
                "origin_model": "pms.folio",
                "origin_res_id": self.folio.id,
                "property_id": self.pms_property1.id,
                "recipient_mode": "partners",
                "recipient_partner_ids": [
                    (
                        6,
                        0,
                        [
                            self.partner1.id,
                            self.partner2.id,
                            self.partner3.id,
                        ],
                    )
                ],
            }
        )
        self.assertEqual(len(logs), 3)

    def test_create_no_split_single_partner(self):
        logs = self.env["pms.notification.log"].create(
            {
                "name": "Single Log",
                "template_id": self.bookai_template.id,
                "channel": "bookai_whatsapp",
                "origin_model": "pms.folio",
                "origin_res_id": self.folio.id,
                "property_id": self.pms_property1.id,
                "recipient_mode": "partners",
                "recipient_partner_ids": [(6, 0, [self.partner1.id])],
            }
        )
        self.assertEqual(len(logs), 1)

    def test_create_no_split_non_bookai_channel(self):
        logs = self.env["pms.notification.log"].create(
            {
                "name": "Email Log",
                "template_id": self.bookai_template.id,
                "channel": "email",
                "origin_model": "pms.folio",
                "origin_res_id": self.folio.id,
                "property_id": self.pms_property1.id,
                "recipient_mode": "partners",
                "recipient_partner_ids": [
                    (
                        6,
                        0,
                        [self.partner1.id, self.partner2.id],
                    )
                ],
            }
        )
        self.assertEqual(len(logs), 1)

    # ---------------------------------------------------------------
    # _extract_m2m_ids
    # ---------------------------------------------------------------
    def test_extract_m2m_ids_command_6(self):
        Log = self.env["pms.notification.log"]
        result = Log._extract_m2m_ids([(6, 0, [1, 2, 3])])
        self.assertEqual(sorted(result), [1, 2, 3])

    def test_extract_m2m_ids_command_4(self):
        Log = self.env["pms.notification.log"]
        result = Log._extract_m2m_ids([(4, 10, False), (4, 20, False)])
        self.assertEqual(sorted(result), [10, 20])

    def test_extract_m2m_ids_plain_list(self):
        Log = self.env["pms.notification.log"]
        result = Log._extract_m2m_ids([1, 2, 3])
        self.assertEqual(len(result), 3)

    def test_extract_m2m_ids_empty(self):
        Log = self.env["pms.notification.log"]
        self.assertEqual(Log._extract_m2m_ids([]), [])

    def test_extract_m2m_ids_none(self):
        Log = self.env["pms.notification.log"]
        self.assertEqual(Log._extract_m2m_ids(None), [])

    # ---------------------------------------------------------------
    # Property mode
    # ---------------------------------------------------------------
    def test_property_mode_disabled_skips(self):
        self.pms_property1.write({"bookai_mode": "disabled"})
        log = self._create_log()
        ok, state, msg = log._bookai_check_property_mode()
        self.assertFalse(ok)
        self.assertEqual(state, "skipped")

    def test_property_mode_manual_with_rule_skips(self):
        self.pms_property1.write({"bookai_mode": "manual"})
        rule = self.env["pms.property.notification.rule"].create(
            {
                "name": "Manual Rule",
                "template_id": self.bookai_template.id,
                "target_model_id": self.folio_model.id,
                "rule_type": "event",
                "event_type": "on_create",
                "event_domain": "[]",
                "channel": "bookai_whatsapp",
                "send_immediately": False,
            }
        )
        log = self._create_log(rule_id=rule.id)
        ok, state, msg = log._bookai_check_property_mode()
        self.assertFalse(ok)

    def test_property_mode_manual_without_rule_ok(self):
        self.pms_property1.write({"bookai_mode": "manual"})
        log = self._create_log()
        ok, state, msg = log._bookai_check_property_mode()
        self.assertTrue(ok)

    def test_property_mode_ai_ok(self):
        self.pms_property1.write({"bookai_mode": "ai"})
        log = self._create_log()
        ok, state, msg = log._bookai_check_property_mode()
        self.assertTrue(ok)

    # ---------------------------------------------------------------
    # Phone normalization
    # ---------------------------------------------------------------
    def test_normalize_phone_e164_passthrough(self):
        log = self._create_log()
        result = log._bookai_normalize_phone("+34600000000", default_country="ES")
        self.assertEqual(result, "+34600000000")

    def test_normalize_phone_00_prefix(self):
        log = self._create_log()
        result = log._bookai_normalize_phone("0034600000000", default_country="ES")
        self.assertTrue(result.startswith("+34"))

    def test_normalize_phone_empty_raises(self):
        log = self._create_log()
        with self.assertRaises(ValidationError):
            log._bookai_normalize_phone("")

    def test_is_e164_valid(self):
        log = self._create_log()
        self.assertTrue(log._bookai_is_e164("+34600000000"))

    def test_is_e164_invalid_no_plus(self):
        log = self._create_log()
        self.assertFalse(log._bookai_is_e164("34600000000"))

    def test_is_e164_invalid_short(self):
        log = self._create_log()
        self.assertFalse(log._bookai_is_e164("+123"))

    def test_guess_country_from_phone_spain(self):
        log = self._create_log()
        result = log._bookai_guess_country_from_phone("+34600000000")
        self.assertEqual(result, "ES")

    def test_guess_country_from_phone_fallback(self):
        log = self._create_log()
        result = log._bookai_guess_country_from_phone("invalid", fallback_country="ES")
        self.assertEqual(result, "ES")

    # ---------------------------------------------------------------
    # Origin folio
    # ---------------------------------------------------------------
    def test_resolve_origin_folio_valid(self):
        log = self._create_log()
        folio = log._bookai_resolve_origin_folio(self.bookai_template, self.folio)
        self.assertEqual(folio.id, self.folio.id)

    def test_resolve_origin_folio_empty_raises(self):
        tmpl = self.env["pms.notification.template"].create(
            {
                "name": "No folio tmpl",
                "code": "no_folio",
                "model_id": self.folio_model.id,
                "bookai_origin_folio_id_tmpl": "",
            }
        )
        log = self._create_log(template_id=tmpl.id)
        with self.assertRaises(ValidationError):
            log._bookai_resolve_origin_folio(tmpl, self.folio)

    def test_resolve_origin_folio_not_found_raises(self):
        tmpl = self.env["pms.notification.template"].create(
            {
                "name": "Bad folio",
                "code": "bad_folio",
                "model_id": self.folio_model.id,
                "bookai_origin_folio_id_tmpl": "{{ 999999 }}",
            }
        )
        log = self._create_log(template_id=tmpl.id)
        with self.assertRaises(ValidationError):
            log._bookai_resolve_origin_folio(tmpl, self.folio)

    # ---------------------------------------------------------------
    # _to_ymd
    # ---------------------------------------------------------------
    def test_to_ymd_datetime(self):
        log = self._create_log()
        result = log._to_ymd(datetime(2026, 4, 24, 10, 30))
        self.assertEqual(result, "2026-04-24")

    def test_to_ymd_date(self):
        log = self._create_log()
        result = log._to_ymd(date(2026, 4, 24))
        self.assertEqual(result, "2026-04-24")

    def test_to_ymd_none(self):
        log = self._create_log()
        self.assertEqual(log._to_ymd(None), "")

    # ---------------------------------------------------------------
    # Build payload
    # ---------------------------------------------------------------
    def test_build_payload_structure(self):
        log = self._create_log()
        log.write(
            {
                "whatsapp_phone": "+34600000000",
                "whatsapp_country": "ES",
                "bookai_origin_folio_id": self.folio.id,
            }
        )
        payload = log._build_bookai_payload(
            phone="+34600000000",
            country="ES",
            template_code="test_v1",
            template_language="es",
            display_name="Guest",
            parameters={"guest_name": "Test"},
        )
        self.assertIn("source", payload)
        self.assertIn("recipient", payload)
        self.assertIn("template", payload)
        self.assertIn("meta", payload)
        self.assertEqual(payload["recipient"]["phone"], "+34600000000")

    # ---------------------------------------------------------------
    # Send request
    # ---------------------------------------------------------------
    def test_send_bookai_request_success(self):
        log = self._create_log()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "message_id": "msg-123",
            "status": "sent",
        }
        mock_resp.content = b'{"message_id":"msg-123"}'
        mock_resp.text = '{"message_id":"msg-123"}'
        mock_resp.raise_for_status = MagicMock()
        with patch(
            "odoo.addons.pms_bookai.models." "pms_notification_log.requests.post",
            return_value=mock_resp,
        ):
            result = log._send_bookai_request({"test": True})
        self.assertEqual(result["message_id"], "msg-123")
        self.assertEqual(log.bookai_last_http_status, 200)

    def test_send_bookai_request_http_error(self):
        log = self._create_log()
        import requests as req

        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.content = b'{"error":"bad"}'
        mock_resp.text = '{"error":"bad"}'
        mock_resp.json.return_value = {"error": "bad"}
        mock_resp.raise_for_status.side_effect = req.exceptions.HTTPError(
            response=mock_resp
        )
        with patch(
            "odoo.addons.pms_bookai.models." "pms_notification_log.requests.post",
            return_value=mock_resp,
        ):
            with self.assertRaises(UserError):
                log._send_bookai_request({"test": True})

    def test_send_bookai_request_timeout(self):
        import requests as req

        log = self._create_log()
        with patch(
            "odoo.addons.pms_bookai.models." "pms_notification_log.requests.post",
            side_effect=req.exceptions.Timeout(),
        ):
            with self.assertRaises(UserError):
                log._send_bookai_request({"test": True})

    def test_send_stores_debug_fields(self):
        log = self._create_log()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {}
        mock_resp.content = b"{}"
        mock_resp.text = "{}"
        mock_resp.raise_for_status = MagicMock()
        with patch(
            "odoo.addons.pms_bookai.models." "pms_notification_log.requests.post",
            return_value=mock_resp,
        ):
            log._send_bookai_request({"payload": "data"})
        self.assertTrue(log.bookai_last_request_payload)
        self.assertTrue(log.bookai_last_request_headers)
        self.assertEqual(log.bookai_last_http_status, 200)

    def test_action_send_skips_already_sent(self):
        log = self._create_log()
        log.write({"state": "sent"})
        with patch(
            "odoo.addons.pms_bookai.models." "pms_notification_log.requests.post"
        ) as mock_post:
            log.action_send_bookai_whatsapp()
        mock_post.assert_not_called()

    def test_action_send_skips_error(self):
        log = self._create_log()
        log.write({"state": "error"})
        with patch(
            "odoo.addons.pms_bookai.models." "pms_notification_log.requests.post"
        ) as mock_post:
            log.action_send_bookai_whatsapp()
        mock_post.assert_not_called()
