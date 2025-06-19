from odoo.tests.common import TransactionCase
from odoo.exceptions import ValidationError


class TestRoomdooAppMenu(TransactionCase):
    def setUp(self):
        super().setUp()
        # Create test property
        self.property = self.env["pms.property"].create(
            {
                "name": "Test Property",
                "company_id": self.env.company.id,
            }
        )

        # Use the existing support menu from data
        self.support_menu = self.env["roomdoo.app.menu"].search(
            [("support_url", "=", True)], limit=1
        )

        # Create test parameters
        self.param_property = self.env["roomdoo.app.menu.url.param"].create(
            {
                "name": "property_id",
                "value": "property.id",
                "menu_id": self.support_menu.id,
            }
        )

        self.param_user = self.env["roomdoo.app.menu.url.param"].create(
            {
                "name": "user",
                "value": "user.name",
                "menu_id": self.support_menu.id,
            }
        )

    def test_support_url_constraint(self):
        """Test that only one menu can be marked as support URL"""
        with self.assertRaises(ValidationError):
            self.env["roomdoo.app.menu"].create(
                {
                    "name": "Second Support",
                    "base_url": "https://support2.example.com",
                    "support_url": True,
                }
            )

    def test_prevent_last_support_url_unset(self):
        """Test that the last support URL cannot be unset"""
        with self.assertRaises(ValidationError):
            self.support_menu.support_url = False

    def test_prevent_last_support_url_delete(self):
        """Test that the last support URL cannot be deleted"""
        with self.assertRaises(ValidationError):
            self.support_menu.unlink()

    def test_url_generation(self):
        """Test URL generation with parameters"""
        url = self.support_menu.generate_url(self.property)
        self.assertTrue(str(self.property.id) in url)
        self.assertTrue(self.env.user.name in url)

    def test_property_menus(self):
        """Test getting menus for a specific property"""
        # Create menu with specific property
        property_menu = self.env["roomdoo.app.menu"].create(
            {
                "name": "Property Menu",
                "base_url": "https://property.example.com",
                "property_ids": [(4, self.property.id)],
            }
        )

        # Create menu without properties
        global_menu = self.env["roomdoo.app.menu"].create(
            {
                "name": "Global Menu",
                "base_url": "https://global.example.com",
            }
        )

        # Get menus for property
        menus = self.property.get_roomdoo_app_menu()
        menu_urls = [menu["url"] for menu in menus]
        self.assertIn(property_menu.generate_url(self.property), menu_urls)
        self.assertIn(global_menu.generate_url(self.property), menu_urls)

    def test_param_evaluation(self):
        """Test parameter value evaluation"""
        value = self.param_property.evaluate_value(self.property)
        self.assertEqual(value, self.property.id)

        value = self.param_user.evaluate_value(self.property)
        self.assertEqual(value, self.env.user.name)
