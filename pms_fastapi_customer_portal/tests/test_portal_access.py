from fastapi import status

from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi_customer_portal.models.fastapi_endpoint import (
    customer_portal_router,
)

from .common import CommonTestCustomerPortalApi

PORTAL_PATHS = {
    "/portal/{token}/folio",
    "/portal/{token}/property",
    "/portal/{token}/checkin-guests",
    "/portal/{token}/checkin-context",
    "/portal/{token}/checkin-partners/{checkin_partner_id}",
    "/portal/{token}/countries",
    "/portal/{token}/country-states",
    "/portal/{token}/id-number-categories",
}
# Check-in requests: method, path after the token and keyword arguments.
CHECKIN_REQUESTS = [
    ("GET", "checkin-guests", {}),
    ("GET", "checkin-context", {}),
    ("GET", "checkin-partners/{guest}", {}),
    ("GET", "countries", {}),
    ("GET", "country-states", {}),
    ("GET", "id-number-categories", {}),
]


class TestPortalTokenAccess(CommonTestCustomerPortalApi):
    """A request reaches only the folio of the exact token in its path."""

    def setUp(self):
        super().setUp()
        self.folio = self._create_folio([(self.double, 3, 2, 2, 0)])
        self.unknown = self._get("/portal/not-a-real-token/folio")
        self.assertEqual(self.unknown.status_code, status.HTTP_404_NOT_FOUND)

    def _assert_same_not_found(self, path):
        response = self._get(path)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, path)
        self.assertEqual(
            response.headers["content-type"], "application/problem+json", path
        )
        self.assertEqual(response.json(), self.unknown.json(), path)

    def test_wildcards_prefixes_and_case_are_not_found(self):
        token = self.folio.access_token
        tokens = [
            "%25",
            "_",
            "%25%25",
            "*",
            "null",
            "False",
            token[:8],
            token[:-1],
            token.upper(),
            token[:-1] + "_",
            token[:8] + "%25",
            "%20" + token,
            token + "%20",
            "%27%20OR%201%3D1%20--",
            "x" * 5000,
        ]
        for bad in tokens:
            for route in ("folio", "property"):
                with self.subTest(token=bad[:40], route=route):
                    self._assert_same_not_found(f"/portal/{bad}/{route}")

    def test_tokens_of_other_models_are_not_found(self):
        reservation = self.folio.reservation_ids
        guest = reservation.checkin_partner_ids[:1]
        self.assertTrue(guest, "The fixture should create a guest")
        other_tokens = {
            "reservation": reservation._portal_ensure_token(),
            "guest": guest._portal_ensure_token(),
        }
        for model, token in other_tokens.items():
            self.assertNotEqual(token, self.folio.access_token)
            for route in ("folio", "property"):
                with self.subTest(model=model, route=route):
                    self._assert_same_not_found(f"/portal/{token}/{route}")

    def test_nul_byte_token_is_not_found(self):
        for route in ("folio", "property"):
            with self.subTest(route=route):
                self._assert_same_not_found(f"/portal/%00/{route}")

    def test_unreachable_folios_answer_like_unknown_tokens(self):
        without_stays = self.env["pms.folio"].create(
            {
                "pms_property_id": self.property.id,
                "partner_id": self.partner.id,
                "partner_name": self.partner.name,
            }
        )
        without_stays.write({"state": "confirm"})
        draft = self._create_folio([(self.single, 5, 1, 1, 0)])
        draft.write({"state": "draft"})
        for folio in (without_stays, draft):
            for route in ("folio", "property"):
                with self.subTest(folio=folio.name, route=route):
                    self._assert_same_not_found(f"/portal/{folio.access_token}/{route}")

    def test_folio_of_its_own_token_only(self):
        other = self._create_folio([(self.single, 5, 1, 1, 0)])

        body = self._get(f"/portal/{self.folio.access_token}/folio").json()

        self.assertEqual(body["id"], self.folio.id)
        self.assertEqual(body["reservationIds"], self.folio.reservation_ids.ids)
        self.assertFalse(set(body["reservationIds"]) & set(other.reservation_ids.ids))

    def test_fully_cancelled_folio_is_reachable_without_stays(self):
        self.folio.reservation_ids.action_cancel()
        self.assertEqual(self.folio.state, "cancel")

        response = self._get(f"/portal/{self.folio.access_token}/folio")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        body = response.json()
        self.assertEqual(body["state"], "cancelled")
        self.assertEqual(body["adults"], 0)
        self.assertEqual(body["completedGuests"], 0)
        self.assertIsNone(body["arrivalHour"])
        self.assertIsNone(body["departureHour"])
        self.assertAlmostEqual(
            sum(line["amount"] for line in body["saleLines"]),
            body["totalAmount"],
            places=2,
        )


class TestPortalCheckinTokenAccess(CommonTestCustomerPortalApi):
    """The check-in serves confirmed folios only, the others answer like an
    unknown token."""

    def setUp(self):
        super().setUp()
        self.unknown = self._get("/portal/not-a-real-token/folio")
        self.assertEqual(self.unknown.status_code, status.HTTP_404_NOT_FOUND)

    def _assert_checkin_not_found(self, token, guest):
        for method, route, kwargs in CHECKIN_REQUESTS:
            path = f"/portal/{token}/{route.format(guest=guest.id)}"
            with self.subTest(method=method, path=path):
                response = self._request(method, path, **kwargs)
                self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
                self.assertEqual(
                    response.headers["content-type"], "application/problem+json"
                )
                self.assertEqual(response.json(), self.unknown.json())
        self.assertFalse(guest.firstname)

    def test_unknown_tokens_are_not_found(self):
        guest = self._create_folio([(self.double, 3, 2, 1, 0)]).checkin_partner_ids
        for token in ("not-a-real-token", "%00", "%25"):
            self._assert_checkin_not_found(token, guest)

    def test_folios_that_are_not_confirmed_are_not_found(self):
        draft = self._create_folio([(self.double, 3, 2, 1, 0)])
        draft.write({"state": "draft"})
        done = self._create_folio([(self.double, 3, 2, 1, 0)])
        done.write({"state": "done"})
        cancelled = self._create_folio([(self.single, 3, 2, 1, 0)])
        cancelled.reservation_ids.action_cancel()
        self.assertEqual(cancelled.state, "cancel")
        for folio in (draft, done, cancelled):
            with self.subTest(state=folio.state):
                self._assert_checkin_not_found(
                    folio.access_token, folio.reservation_ids.checkin_partner_ids
                )

    def test_confirmed_folio_is_served(self):
        folio = self._create_folio([(self.double, 3, 2, 1, 0)])
        guest = folio.checkin_partner_ids
        for method, route, kwargs in CHECKIN_REQUESTS:
            path = f"/portal/{folio.access_token}/{route.format(guest=guest.id)}"
            with self.subTest(method=method, path=path):
                response = self._request(method, path, **kwargs)
                self.assertNotEqual(
                    response.status_code, status.HTTP_404_NOT_FOUND, response.text
                )


class TestPortalAppBoundary(CommonTestCustomerPortalApi):
    def test_portal_app_serves_only_portal_routes(self):
        portal = self.env["fastapi.endpoint"].new({"app": "customer_portal"})
        staff = self.env["fastapi.endpoint"].new({"app": "pms_api"})

        self.assertEqual(portal._get_fastapi_routers(), [customer_portal_router])
        self.assertEqual(staff._get_fastapi_routers(), [pms_api_router])
        self.assertEqual(
            {route.path for route in customer_portal_router.routes}, PORTAL_PATHS
        )
        self.assertFalse(PORTAL_PATHS & {route.path for route in pms_api_router.routes})
        app_paths = {route.path for route in self.portal_app._get_app().routes}
        self.assertTrue(PORTAL_PATHS <= app_paths)
        self.assertFalse(
            {route.path for route in pms_api_router.routes} & app_paths - PORTAL_PATHS
        )

    def test_no_session_even_when_the_endpoint_saves_sessions(self):
        self.portal_app.save_http_session = True

        self.assertIs(self.portal_app._get_routing_info()["save_session"], False)

    def test_not_found_carries_cors_headers(self):
        app = self.portal_app._get_app()
        with self._create_test_client(app=app) as client:
            response = client.get(
                "/portal/not-a-real-token/folio",
                headers={"Origin": "https://elsewhere.example.com"},
            )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.headers["access-control-allow-origin"], "*")
        self.assertNotIn("access-control-allow-credentials", response.headers)

    def test_preflight_from_other_origin_is_refused_with_portal_url(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "roomdoo_customer_portal_url", "https://portal.example.com"
        )
        app = self.portal_app._get_app()
        with self._create_test_client(app=app) as client:
            response = client.options(
                "/portal/x/folio",
                headers={
                    "Origin": "https://elsewhere.example.com",
                    "Access-Control-Request-Method": "GET",
                },
            )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertNotIn("access-control-allow-origin", response.headers)

    def test_write_methods_are_not_allowed(self):
        folio = self._create_folio([(self.double, 3, 2, 2, 0)])
        with self._create_test_client() as client:
            response = client.post(f"/portal/{folio.access_token}/folio", json={})

        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
