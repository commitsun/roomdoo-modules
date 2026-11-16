# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""The credentials of BookAI only reach the staff.

The browser never gets the instance token: the staff exchange their session
for a short-lived token of their own, signed with a key whose public half is
published for BookAI. Customers get no token, the LLM keys stay with BookAI
administrators and the register wizard only loads the stored credentials for
those allowed to use it.
"""

import hashlib
import json

import jwt
from fastapi import HTTPException

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import tagged

from odoo.addons.pms_bookai.routers.bookai_config import (
    USER_TOKEN_KEY_PARAM,
    USER_TOKEN_TTL,
)

from .common import TestBookaiCommon

INSTANCE_TOKEN = "test-token-123"


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

    def _helper(self, user):
        return self.env["pms_api_bookai.config_router.helper"].with_user(user)

    def _public_key(self):
        jwks = self._helper(self.customer).get_jwks()
        self.assertEqual(len(jwks["keys"]), 1)
        jwk = jwks["keys"][0]
        return jwk["kid"], jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))

    def _decode(self, token):
        kid, public_key = self._public_key()
        self.assertEqual(jwt.get_unverified_header(token)["kid"], kid)
        return jwt.decode(token, public_key, algorithms=["RS256"], audience="bookai")

    def test_a_customer_gets_no_token(self):
        self.assertEqual(self._helper(self.customer)._get_bookai_token(), "")
        with self.assertRaises(HTTPException) as refused:
            self._helper(self.customer).get_user_token()
        self.assertEqual(refused.exception.status_code, 403)

    def test_the_staff_get_a_token_of_their_own(self):
        for user in (self.receptionist, self.employee):
            with self.subTest(user=user.login):
                answer = self._helper(user).get_user_token()
                self.assertNotEqual(answer.token, INSTANCE_TOKEN)
                self.assertEqual(answer.expires_in, USER_TOKEN_TTL)
                claims = self._decode(answer.token)
                self.assertEqual(claims["sub"], str(user.id))
                self.assertEqual(claims["scope"], "chat")
                self.assertEqual(claims["props"], sorted(user.pms_property_ids.ids))
                self.assertEqual(claims["exp"] - claims["iat"], USER_TOKEN_TTL)
                self.assertEqual(
                    claims["bkh"],
                    hashlib.sha256(INSTANCE_TOKEN.encode()).hexdigest()[:32],
                )

    def test_the_config_never_carries_the_instance_token(self):
        config = self._helper(self.receptionist).get_config()
        self.assertTrue(config.bookaiEnabled)
        self.assertNotEqual(config.bookaiToken, INSTANCE_TOKEN)
        self.assertEqual(
            self._decode(config.bookaiToken)["sub"], str(self.receptionist.id)
        )

    def test_no_token_without_bookai(self):
        self.icp.set_param("pms_bookai.api_token", "")
        with self.assertRaises(HTTPException) as refused:
            self._helper(self.receptionist).get_user_token()
        self.assertEqual(refused.exception.status_code, 404)

    def test_the_signing_key_is_kept_and_can_be_replaced(self):
        kid, _key = self._public_key()
        self.assertEqual(self._public_key()[0], kid)
        self.icp.set_param(USER_TOKEN_KEY_PARAM, False)
        self.assertNotEqual(self._public_key()[0], kid)

    def test_a_token_signed_with_another_key_is_rejected(self):
        token = self._helper(self.receptionist).get_user_token().token
        self.icp.set_param(USER_TOKEN_KEY_PARAM, False)
        _kid, new_public_key = self._public_key()
        with self.assertRaises(jwt.InvalidSignatureError):
            jwt.decode(token, new_public_key, algorithms=["RS256"], audience="bookai")

    def test_the_llm_key_is_for_bookai_administrators_only(self):
        account = self.llm_account.with_user(self.employee)
        self.assertEqual(account.read(["name"])[0]["name"], "Test LLM")
        with self.assertRaises(AccessError):
            account.read(["api_key"])

    def test_the_register_wizard_keeps_its_credentials(self):
        wizard = self.env["bookai.register.wizard"].with_user(self.employee)
        with self.assertRaises(AccessError):
            wizard.default_get(["odoo_api_key"])
