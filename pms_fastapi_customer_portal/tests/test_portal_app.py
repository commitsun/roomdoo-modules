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


class TestPortalCheckinCors(CommonTestCustomerPortalApi):
    """A browser on another origin may send the check-in writes."""

    def setUp(self):
        super().setUp()
        self.folio = self._create_folio([(self.double, 3, 2, 1, 0)])
        self.guest = self.folio.checkin_partner_ids
        self.url = f"/portal/{self.folio.access_token}/checkin-partners/{self.guest.id}"

    def _preflight(self, method, origin=OTHER_URL, content_type=True):
        headers = {"Origin": origin, "Access-Control-Request-Method": method}
        if content_type:
            headers["Access-Control-Request-Headers"] = "content-type"
        app = self.portal_app._get_app()
        with self._create_test_client(app=app) as test_client:
            return test_client.options(self.url, headers=headers)

    def _assert_preflight_allowed(self, method, origin):
        response = self._preflight(method, origin)

        self.assertEqual(response.status_code, 200, response.text)
        allowed = response.headers["access-control-allow-methods"].split(", ")
        self.assertIn(method, allowed)
        self.assertEqual(
            response.headers["access-control-allow-headers"].lower(), "content-type"
        )
        return response

    def test_preflight_allows_the_merge_patch(self):
        response = self._assert_preflight_allowed("PATCH", OTHER_URL)

        self.assertEqual(response.headers["access-control-allow-origin"], "*")
        self.assertNotIn("access-control-allow-credentials", response.headers)

    def test_preflight_from_the_portal_origin_with_portal_url(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "roomdoo_customer_portal_url", PORTAL_URL
        )

        response = self._assert_preflight_allowed("PATCH", PORTAL_URL)

        self.assertEqual(response.headers["access-control-allow-origin"], PORTAL_URL)
        self.assertEqual(self._preflight("PATCH", OTHER_URL).status_code, 400)

    def test_preflight_refuses_other_methods(self):
        for method in ("PUT", "DELETE"):
            with self.subTest(method=method):
                response = self._preflight(method)

                self.assertEqual(response.status_code, 400, response.text)
                allowed = response.headers["access-control-allow-methods"]
                self.assertNotIn(method, allowed.split(", "))

    def test_patch_carries_cors_headers(self):
        app = self.portal_app._get_app()
        with self._create_test_client(app=app) as test_client:
            response = test_client.patch(
                self.url,
                content='{"firstname": "Ann"}',
                headers={
                    "Origin": OTHER_URL,
                    "Content-Type": "application/merge-patch+json",
                },
            )

        self.assertEqual(response.status_code, 204, response.text)
        self.assertEqual(response.headers["access-control-allow-origin"], "*")
        self.assertEqual(self.guest.firstname, "Ann")
