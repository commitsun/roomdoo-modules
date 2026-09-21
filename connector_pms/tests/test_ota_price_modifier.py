# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestOtaPriceModifier(TransactionCase):
    """The modifier is typed once on the agency and read by every connector,
    so the only thing this module owns is that it cannot be typed wrong."""

    def setUp(self):
        super().setUp()
        # A plain partner: making one a real agency drags in the pms pricelist
        # rules, and the modifier is not gated on any of that.
        self.agency = self.env["res.partner"].create({"name": "An agency"})

    def test_a_rate_logic_without_a_value_is_refused(self):
        with self.assertRaises(ValidationError):
            self.agency.ota_price_modifier_type = "increase_percent"

    def test_a_value_without_a_rate_logic_is_refused(self):
        with self.assertRaises(ValidationError):
            self.agency.ota_price_modifier_value = 10

    def test_a_negative_value_is_refused(self):
        """The direction is the rate logic, never the sign."""
        with self.assertRaises(ValidationError):
            self.agency.write(
                {
                    "ota_price_modifier_type": "increase_percent",
                    "ota_price_modifier_value": -10,
                }
            )

    def test_a_full_discount_is_refused(self):
        with self.assertRaises(ValidationError):
            self.agency.write(
                {
                    "ota_price_modifier_type": "decrease_percent",
                    "ota_price_modifier_value": 100,
                }
            )

    def test_a_discount_just_short_of_everything_is_allowed(self):
        self.agency.write(
            {
                "ota_price_modifier_type": "decrease_percent",
                "ota_price_modifier_value": 99.5,
            }
        )
        self.assertEqual(self.agency.ota_price_modifier_value, 99.5)
