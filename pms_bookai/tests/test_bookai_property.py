from unittest.mock import patch

from odoo.tests import tagged

from .common import TestBookaiCommon

WEBHOOK_PATH = "odoo.addons.pms_bookai.models.bookai_webhook_mixin.requests.post"


@tagged("post_install", "-at_install")
class TestBookaiProperty(TestBookaiCommon):
    def test_webhook_on_watched_field_change(self):
        with patch(WEBHOOK_PATH) as mock_post:
            self.pms_property1.write({"bookai_mode": "ai"})
        self.assertTrue(mock_post.called)

    def test_no_webhook_on_unwatched_field(self):
        with patch(WEBHOOK_PATH) as mock_post:
            self.pms_property1.write({"default_pricelist_id": self.pricelist1.id})
        mock_post.assert_not_called()

    def test_hotel_config_info_structure(self):
        self.pms_property1.write(
            {
                "bookai_mode": "ai",
                "external_code": "TEST01",
            }
        )
        info = self.pms_property1.get_bookai_hotel_config_info()
        self.assertEqual(info["id"], self.pms_property1.id)
        self.assertEqual(info["external_code"], "TEST01")
        self.assertEqual(info["bookai_mode"], "ai")
        self.assertIn("bookai_escalation_timeout", info)
        self.assertIn("bookai_wa_phone_number_id", info)

    def test_hotel_config_info_escalation_contacts(self):
        user_with_phone = self.env["res.users"].create(
            {
                "name": "Escalation User",
                "login": "esc@test.com",
                "mobile": "+34600111222",
            }
        )
        user_no_phone = self.env["res.users"].create(
            {
                "name": "No Phone User",
                "login": "nophone@test.com",
            }
        )
        self.pms_property1.write(
            {
                "bookai_escalation_user_ids": [
                    (6, 0, [user_with_phone.id, user_no_phone.id])
                ]
            }
        )
        info = self.pms_property1.get_bookai_hotel_config_info()
        contacts = info["bookai_escalation_contacts"]
        contact_ids = [c["user_id"] for c in contacts]
        self.assertIn(user_with_phone.id, contact_ids)
        self.assertNotIn(user_no_phone.id, contact_ids)

    def test_hotel_public_info_no_sensitive_data(self):
        self.pms_property1.write(
            {
                "bookai_wa_access_token": "secret-token",
                "bookai_wa_phone_number_id": "12345",
            }
        )
        info = self.pms_property1.get_bookai_hotel_public_info()
        self.assertNotIn("bookai_wa_access_token", info)
        self.assertNotIn("bookai_escalation_contacts", info)
        self.assertIn("name", info)
        self.assertIn("tz", info)

    def test_get_bookai_prices_date_range(self):
        room_type = self.env["pms.room.type"].create(
            {
                "name": "Test Room Type",
                "default_code": "TRT",
                "class_id": self.room_type_class1.id,
            }
        )
        result = self.env["pms.property"].get_bookai_prices(
            property_id=self.pms_property1.id,
            pricelist_id=self.pricelist1.id,
            room_type_id=room_type.id,
            date_from="2026-06-01",
            date_to="2026-06-04",
        )
        self.assertEqual(len(result), 3)
        self.assertIn("date", result[0])
        self.assertIn("price", result[0])

    def test_get_bookai_prices_string_dates(self):
        room_type = self.env["pms.room.type"].create(
            {
                "name": "Test RT Str",
                "default_code": "TRS",
                "class_id": self.room_type_class1.id,
            }
        )
        result = self.env["pms.property"].get_bookai_prices(
            property_id=self.pms_property1.id,
            pricelist_id=self.pricelist1.id,
            room_type_id=room_type.id,
            date_from="2026-07-01",
            date_to="2026-07-02",
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["date"], "2026-07-01")
