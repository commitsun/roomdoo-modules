from fastapi import status

from odoo.addons.pms_fastapi.tests.common import CommonTestRoomdooApi

from ..schemas.user import User


class TestUserEndpoints(CommonTestRoomdooApi):
    def test_user_get(self):
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.get("/user")
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            User(**response.json())
