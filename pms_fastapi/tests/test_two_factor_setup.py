import base64
import time
from urllib.parse import parse_qs, unquote, urlparse

from fastapi import status

from odoo.addons.auth_totp.models.totp import hotp
from odoo.addons.pms_fastapi.tests.common import CommonTestPmsApi


class TestTwoFactorSetup(CommonTestPmsApi):
    def _offer(self, test_client):
        response = test_client.get("/user/two-factor")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        return response.json()

    def _code_for(self, secret):
        return str(hotp(base64.b32decode(secret), int(time.time() / 30)))

    def _activate(self, test_client, code, password="supersecret"):
        return test_client.post(
            "/user/two-factor", json={"code": code, "password": password}
        )

    def test_the_offer_can_be_read_by_an_authentication_app(self):
        with self._create_test_client() as test_client:
            self._login(test_client)

            offer = self._offer(test_client)

            parsed = urlparse(offer["uri"])
            self.assertEqual(parsed.scheme, "otpauth")
            self.assertEqual(parsed.netloc, "totp")
            self.assertIn(self.test_user.login, unquote(parsed.path))
            self.assertEqual(parse_qs(parsed.query)["secret"], [offer["secret"]])

    def test_the_offer_names_the_address_the_client_is_reached_at(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "roomdoo_app_url", "https://tenant.example.com"
        )
        with self._create_test_client() as test_client:
            self._login(test_client)

            offer = self._offer(test_client)

            self.assertEqual(
                parse_qs(urlparse(offer["uri"]).query)["issuer"],
                ["tenant.example.com"],
            )

    def test_the_offer_protects_the_account_once_confirmed(self):
        with self._create_test_client() as test_client:
            self._login(test_client)
            offer = self._offer(test_client)

            response = self._activate(test_client, self._code_for(offer["secret"]))

            self.assertEqual(
                response.status_code, status.HTTP_204_NO_CONTENT, response.text
            )
            self.assertTrue(self.test_user.sudo().totp_enabled)

    def test_a_wrong_password_leaves_the_account_as_it_was(self):
        with self._create_test_client() as test_client:
            self._login(test_client)
            offer = self._offer(test_client)

            response = self._activate(
                test_client, self._code_for(offer["secret"]), password="nope"
            )

            self.assertEqual(response.json()["type"], "/errors/invalid-credentials")
            self.assertFalse(self.test_user.sudo().totp_enabled)

    def test_a_wrong_code_leaves_the_account_as_it_was(self):
        with self._create_test_client() as test_client:
            self._login(test_client)
            offer = self._offer(test_client)
            wrong = str((int(self._code_for(offer["secret"])) + 500000) % 1000000)

            response = self._activate(test_client, wrong)

            self.assertEqual(response.json()["type"], "/errors/mfa-invalid-code")
            self.assertFalse(self.test_user.sudo().totp_enabled)

    def test_nothing_can_be_confirmed_that_was_not_offered(self):
        with self._create_test_client() as test_client:
            self._login(test_client)

            response = self._activate(test_client, "123456")

            self.assertEqual(response.json()["type"], "/errors/mfa-setup-required")

    def test_a_protected_account_is_not_offered_another_one(self):
        with self._create_test_client() as test_client:
            self._login(test_client)
            offer = self._offer(test_client)
            self._activate(test_client, self._code_for(offer["secret"]))

            response = test_client.get("/user/two-factor")

            self.assertEqual(response.json()["type"], "/errors/mfa-already-enabled")
