# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""The instance token and the credentials of BookAI only reach the staff:
customers get no token, the LLM keys stay with BookAI administrators and the
register wizard only loads the stored credentials for those allowed to use it.
"""

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import TestBookaiCommon


@tagged("post_install", "-at_install")
class TestBookaiStaffSecrets(TestBookaiCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        users = cls.env["res.users"].with_context(no_reset_password=True)
        portal = cls.env.ref("base.group_portal")
        cls.customer = users.create(
            {
                "name": "Customer",
                "login": "customer@example.org",
                "groups_id": [Command.set([portal.id])],
            }
        )
        cls.receptionist = users.create(
            {
                "name": "Receptionist",
                "login": "receptionist@example.org",
                "groups_id": [Command.set([portal.id])],
                "company_id": cls.pms_property1.company_id.id,
                "company_ids": [Command.set([cls.pms_property1.company_id.id])],
                "pms_property_ids": [Command.set([cls.pms_property1.id])],
            }
        )
        # A BookAI user reads the LLM accounts, but not their keys
        cls.employee = users.create(
            {
                "name": "Employee",
                "login": "employee@example.org",
                "groups_id": [
                    Command.set(
                        [
                            cls.env.ref("base.group_user").id,
                            cls.env.ref("pms_bookai.group_bookai_user").id,
                        ]
                    )
                ],
            }
        )
        cls.icp.set_param("pms_bookai.odoo_api_key", "stored-key")
        # A BookAI user sees the LLM accounts of the agents assigned to it
        cls.agent.allowed_user_ids = [Command.link(cls.employee.id)]

    def _token_for(self, user):
        helper = self.env["pms_api_bookai.config_router.helper"].with_user(user)
        return helper._get_bookai_token()

    def test_a_customer_gets_no_token(self):
        self.assertEqual(self._token_for(self.customer), "")

    def test_the_staff_get_the_token(self):
        for user in (self.receptionist, self.employee):
            with self.subTest(user=user.login):
                self.assertEqual(self._token_for(user), "test-token-123")

    def test_the_llm_key_is_for_bookai_administrators_only(self):
        account = self.llm_account.with_user(self.employee)
        self.assertEqual(account.read(["name"])[0]["name"], "Test LLM")
        with self.assertRaises(AccessError):
            account.read(["api_key"])

    def test_the_register_wizard_keeps_its_credentials(self):
        wizard = self.env["bookai.register.wizard"].with_user(self.employee)
        with self.assertRaises(AccessError):
            wizard.default_get(["odoo_api_key"])
