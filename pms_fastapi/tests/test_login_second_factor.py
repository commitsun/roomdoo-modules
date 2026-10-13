import base64
import os
import time

from fastapi import status

from odoo.addons.auth_totp.models.totp import hotp
from odoo.addons.pms_fastapi.tests.common import CommonTestPmsApi

CREDENTIALS = {"username": "test_pms_api", "password": "supersecret"}


class TestLoginSecondFactor(CommonTestPmsApi):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.secret = base64.b32encode(os.urandom(20)).decode()
        cls.test_user.sudo().totp_secret = cls.secret

    def _valid_code(self):
        key = base64.b32decode(self.secret)
        return str(hotp(key, int(time.time() / 30)))

    def _wrong_code(self):
        return str((int(self._valid_code()) + 500000) % 1000000)

    def _problem(self, response):
        self.assertEqual(
            response.status_code, status.HTTP_401_UNAUTHORIZED, response.text
        )
        return response.json()["type"]

    def test_password_alone_is_not_enough(self):
        with self._create_test_client() as test_client:
            response = test_client.post("/login", json=CREDENTIALS)

            self.assertEqual(self._problem(response), "/errors/mfa-required")

    def test_valid_code_completes_the_login(self):
        with self._create_test_client() as test_client:
            test_client.post("/login", json=CREDENTIALS)

            response = test_client.post(
                "/login/mfa", json={"code": self._valid_code(), "remember": False}
            )

            self.assertEqual(
                response.status_code, status.HTTP_204_NO_CONTENT, response.text
            )
            authenticated = test_client.get("/agencies")
            self.assertEqual(authenticated.status_code, status.HTTP_200_OK)

    def test_wrong_code_can_be_tried_again(self):
        with self._create_test_client() as test_client:
            test_client.post("/login", json=CREDENTIALS)

            response = test_client.post(
                "/login/mfa", json={"code": self._wrong_code(), "remember": False}
            )
            self.assertEqual(self._problem(response), "/errors/mfa-invalid-code")

            retried = test_client.post(
                "/login/mfa", json={"code": self._valid_code(), "remember": False}
            )
            self.assertEqual(
                retried.status_code, status.HTTP_204_NO_CONTENT, retried.text
            )

    def test_enough_wrong_codes_end_the_login(self):
        with self._create_test_client() as test_client:
            test_client.post("/login", json=CREDENTIALS)

            for _attempt in range(4):
                response = test_client.post(
                    "/login/mfa", json={"code": self._wrong_code(), "remember": False}
                )
                self.assertEqual(self._problem(response), "/errors/mfa-invalid-code")

            response = test_client.post(
                "/login/mfa", json={"code": self._wrong_code(), "remember": False}
            )
            self.assertEqual(self._problem(response), "/errors/mfa-too-many-attempts")

            # The challenge is gone, so even the right code is of no use now.
            response = test_client.post(
                "/login/mfa", json={"code": self._valid_code(), "remember": False}
            )
            self.assertEqual(self._problem(response), "/errors/mfa-too-many-attempts")

    def test_a_code_is_useless_without_its_login(self):
        with self._create_test_client() as test_client:
            response = test_client.post(
                "/login/mfa", json={"code": self._valid_code(), "remember": False}
            )

            self.assertEqual(self._problem(response), "/errors/mfa-too-many-attempts")

    def test_a_remembered_device_is_not_asked_again(self):
        with self._create_test_client() as test_client:
            test_client.post("/login", json=CREDENTIALS)
            test_client.post(
                "/login/mfa", json={"code": self._valid_code(), "remember": True}
            )

            response = test_client.post("/login", json=CREDENTIALS)

            self.assertEqual(
                response.status_code, status.HTTP_204_NO_CONTENT, response.text
            )

    def test_a_device_is_remembered_only_when_asked(self):
        with self._create_test_client() as test_client:
            test_client.post("/login", json=CREDENTIALS)
            test_client.post(
                "/login/mfa", json={"code": self._valid_code(), "remember": False}
            )

            response = test_client.post("/login", json=CREDENTIALS)

            self.assertEqual(self._problem(response), "/errors/mfa-required")
