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
        cls.validator = cls.env["auth.jwt.validator"]._get_validator_by_name("api_pms")

    def _jwt_secrets(self):
        return self.validator.secret_key, self.validator.refresh_token_secret

    def assertJwtSecretsRenewed(self, old_secrets):
        new_secret_key, new_refresh_secret = self._jwt_secrets()
        self.assertNotEqual(new_secret_key, old_secrets[0])
        self.assertNotEqual(new_refresh_secret, old_secrets[1])

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

    def test_create_instance_flag_renews_jwt_secrets(self):
        """Creating a flag active for the whole instance logs everybody out."""
        old_secrets = self._jwt_secrets()
        self.FeatureFlag.create({"name": "new_flag", "is_active_instance": True})
        self.assertJwtSecretsRenewed(old_secrets)

    def test_create_user_flag_keeps_jwt_secrets(self):
        """Creating a flag for some users keeps everybody logged in."""
        old_secrets = self._jwt_secrets()
        self.FeatureFlag.create({"name": "new_flag", "user_ids": [(4, self.user1.id)]})
        self.assertEqual(self._jwt_secrets(), old_secrets)

    def test_activate_flag_for_instance_renews_jwt_secrets(self):
        """Activating a flag for the whole instance logs everybody out."""
        old_secrets = self._jwt_secrets()
        self.flag_user.is_active_instance = True
        self.assertJwtSecretsRenewed(old_secrets)

    def test_write_on_instance_flag_keeps_jwt_secrets(self):
        """Writing on a flag already active for the instance keeps everybody
        logged in."""
        old_secrets = self._jwt_secrets()
        self.flag_instance.write(
            {"description": "Still active for all", "is_active_instance": True}
        )
        self.assertEqual(self._jwt_secrets(), old_secrets)
