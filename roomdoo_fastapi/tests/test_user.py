from fastapi import status

from odoo.addons.pms_fastapi.tests.common import CommonTestPmsApi


class CommonTestAuth(CommonTestPmsApi):
    def test_password_change(self):
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            payload = {"oldPassword": "supersecret", "newPassword": "supersecret1"}
            response = test_client.patch("/user/change-password", json=payload)
            self.assertEqual(
                response.status_code, status.HTTP_204_NO_CONTENT, response.text
            )
            response = test_client.patch("/user/change-password", json=payload)
            self.assertEqual(
                response.status_code, status.HTTP_401_UNAUTHORIZED, response.text
            )

    def test_reset_password_mail(self):
        with self._create_test_client() as test_client:
            self.test_user.signup_token = None
            self.test_user.password = "a_different_password"
            payload = {"email": self.test_user.email}
            response = test_client.post("/send-mail-reset-password", json=payload)
            self.assertEqual(
                response.status_code, status.HTTP_204_NO_CONTENT, response.text
            )
            reset_password_payload = {
                "resetToken": self.test_user.signup_token,
                "newPassword": "supersecret",
            }
            response = test_client.patch("/reset-password", json=reset_password_payload)
            self.assertEqual(
                response.status_code, status.HTTP_204_NO_CONTENT, response.text
            )
            response = self._login(test_client)

            payload = {"email": "not_exists@example.org"}
            self.assertEqual(
                response.status_code, status.HTTP_204_NO_CONTENT, response.text
            )

    def test_get_availability_rule_fields(self):
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.get("/user/availability-rule-fields")
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
