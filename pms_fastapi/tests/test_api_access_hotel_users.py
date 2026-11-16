"""A portal user without a PMS role cannot get a session here, and the one it
already had stops working, while the refusal is enforced."""
from fastapi import status

from odoo.addons.auth_jwt.exceptions import UnauthorizedInvalidToken
from odoo.addons.fastapi.error_handlers import convert_exception_to_status_body
from odoo.addons.pms_api_rest.models.res_users import API_ACCESS_ENFORCE_PARAM
from odoo.addons.pms_fastapi.tests.common import CommonTestPmsApi

CREDENTIALS = {"username": "test_pms_api", "password": "supersecret"}


class TestApiAccessHotelUsers(CommonTestPmsApi):
    def _enforce(self, enabled=True):
        self.env["ir.config_parameter"].sudo().set_param(
            API_ACCESS_ENFORCE_PARAM, "1" if enabled else "0"
        )

    def _make_portal(self, role=False):
        self.test_user.sudo().write(
            {
                "groups_id": [(6, 0, [self.env.ref("base.group_portal").id])],
                "pms_api_user_role": role,
            }
        )

    def test_a_portal_user_without_a_role_cannot_log_in(self):
        self._make_portal()
        self._enforce()
        with self._create_test_client() as test_client:
            response = test_client.post("/login", json=CREDENTIALS)
            self.assertEqual(
                response.status_code, status.HTTP_401_UNAUTHORIZED, response.text
            )
            self.assertEqual(response.json()["type"], "/errors/invalid-credentials")

    def test_until_enforced_the_portal_user_still_logs_in(self):
        self._make_portal()
        self._enforce(False)
        with self._create_test_client() as test_client:
            self._login(test_client)

    def test_a_portal_user_with_a_role_logs_in(self):
        self._make_portal(role="receptionist")
        self._enforce()
        with self._create_test_client() as test_client:
            self._login(test_client)

    def test_an_internal_user_without_a_role_keeps_working(self):
        self._enforce()
        self.assertFalse(self.test_user.pms_api_user_role)
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get("/agencies")
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)

    def test_an_open_session_ends_when_the_role_is_withdrawn(self):
        self._make_portal(role="receptionist")
        self._enforce()
        with self._create_test_client() as test_client:
            self._login(test_client)
            self.test_user.sudo().pms_api_user_role = False
            # The test client stops at the exception; outside tests the Odoo
            # dispatcher turns it into the 401 checked below.
            with self.assertRaises(UnauthorizedInvalidToken):
                test_client.get("/agencies")
        status_code, _body = convert_exception_to_status_body(
            UnauthorizedInvalidToken()
        )
        self.assertEqual(status_code, status.HTTP_401_UNAUTHORIZED)
