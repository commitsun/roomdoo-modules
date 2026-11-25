from .common import CommonTestCustomerPortalApi

PORTAL_URL = "https://portal.example.com"
OTHER_URL = "https://elsewhere.example.com"


class TestPortalApp(CommonTestCustomerPortalApi):
    def setUp(self):
        super().setUp()
        self.folio = self._create_folio([(self.double, 3, 2, 2, 0)])

    def _get_from(self, origin):
        app = self.portal_app._get_app()
        with self._create_test_client(app=app) as test_client:
            return test_client.get(
                f"/portal/{self.folio.access_token}/folio",
                headers={"Origin": origin},
            )

    def test_any_origin_without_portal_url(self):
        response = self._get_from(OTHER_URL)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["access-control-allow-origin"], "*")

    def test_only_the_portal_origin_with_portal_url(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "roomdoo_customer_portal_url", PORTAL_URL
        )

        allowed = self._get_from(PORTAL_URL)
        other = self._get_from(OTHER_URL)

        self.assertEqual(allowed.headers["access-control-allow-origin"], PORTAL_URL)
        self.assertNotIn("access-control-allow-origin", other.headers)
