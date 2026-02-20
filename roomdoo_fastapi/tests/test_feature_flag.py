from odoo.tests.common import TransactionCase


class TestFeatureFlag(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.FeatureFlag = cls.env["feature.flag"]
        cls.user1 = cls.env["res.users"].create(
            {
                "name": "Test User 1",
                "login": "test_ff_user1@example.com",
                "email": "test_ff_user1@example.com",
            }
        )
        cls.user2 = cls.env["res.users"].create(
            {
                "name": "Test User 2",
                "login": "test_ff_user2@example.com",
                "email": "test_ff_user2@example.com",
            }
        )
        cls.flag_instance = cls.FeatureFlag.create(
            {
                "name": "flag_instance",
                "description": "Active for all",
                "is_active_instance": True,
            }
        )
        cls.flag_user = cls.FeatureFlag.create(
            {
                "name": "flag_user",
                "description": "Active for specific users",
                "is_active_instance": False,
                "user_ids": [(4, cls.user1.id)],
            }
        )

    def test_get_active_for_user_instance_flag(self):
        """An instance-wide flag is returned for any user."""
        result = self.FeatureFlag.get_active_for_user(self.user2)
        self.assertIn("flag_instance", result)

    def test_get_active_for_user_user_flag(self):
        """A user-specific flag is returned only for assigned users."""
        result_user1 = self.FeatureFlag.get_active_for_user(self.user1)
        result_user2 = self.FeatureFlag.get_active_for_user(self.user2)
        self.assertIn("flag_user", result_user1)
        self.assertNotIn("flag_user", result_user2)
