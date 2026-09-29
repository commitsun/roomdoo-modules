from fastapi import status

from odoo.addons.pms_fastapi.tests.common import CommonTestPmsApi


class TestLogout(CommonTestPmsApi):
    def test_the_session_no_longer_opens_anything(self):
        with self._create_test_client() as test_client:
            self._login(test_client)
            self.assertEqual(test_client.get("/user").status_code, status.HTTP_200_OK)

            response = test_client.post("/logout")

            self.assertEqual(
                response.status_code, status.HTTP_204_NO_CONTENT, response.text
            )
            self.assertNotEqual(
                test_client.get("/user").status_code, status.HTTP_200_OK
            )

    def test_what_held_the_session_is_gone_from_the_client(self):
        with self._create_test_client() as test_client:
            self._login(test_client)

            test_client.post("/logout")

            self.assertNotIn("authorization", test_client.cookies)

    def test_leaving_without_a_session_is_not_an_error(self):
        with self._create_test_client() as test_client:
            response = test_client.post("/logout")

            self.assertEqual(
                response.status_code, status.HTTP_204_NO_CONTENT, response.text
            )
