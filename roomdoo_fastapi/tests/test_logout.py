from odoo.addons.pms_fastapi.tests.common import CommonTestPmsApi


class TestLogoutRefreshCookie(CommonTestPmsApi):
    def test_the_refresh_cookie_is_dropped_as_well(self):
        with self._create_test_client() as test_client:
            self._login(test_client)
            self.assertIn("refresh", test_client.cookies)

            test_client.post("/logout")

            self.assertNotIn("refresh", test_client.cookies)
