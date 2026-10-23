# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo.tests import tagged

from odoo.addons.pms_fastapi_booking_engine.tests.common import BookingEngineCase


@tagged("post_install", "-at_install")
class TestAvailabilityGuestTexts(BookingEngineCase):
    def test_guest_facing_texts(self):
        self.pricelist_flexible.guest_rate_name = "Flexible rate"
        self.double.write(
            {
                "guest_display_name": "Double room with a view",
                "bed_configuration_text": "1 double bed",
            }
        )

        response = self._get_availability()

        data = response.json()
        self.assertEqual(data["ratePlans"][0]["name"], "Flexible rate")
        double = self._room_type(response, self.double)
        self.assertEqual(double["name"], "Double room with a view")
        self.assertEqual(double["beds"], "1 double bed")

    def test_without_guest_texts(self):
        response = self._get_availability()

        self.assertEqual(response.json()["ratePlans"][0]["name"], "Flexible")
        double = self._room_type(response, self.double)
        self.assertEqual(double["name"], "Double")
        self.assertEqual(double["beds"], "")
