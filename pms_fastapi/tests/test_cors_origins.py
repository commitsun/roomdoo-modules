# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""Besides the web app at ``roomdoo_app_url``, other clients served from their
own host may call this API from a browser once their origins are listed in
``roomdoo_app_extra_origins``. ``Access-Control-Allow-Origin`` holds a single
origin, so each request is answered with its own, and any origin left out is
refused as before.
"""
from fastapi import status

from odoo.tests import HttpCase, tagged
from odoo.tools import mute_logger

from odoo.addons.fastapi.pools import fastapi_app_pool
from odoo.addons.pms_fastapi.tests.common import CommonTestPmsApi

APP = "https://app.example.com"
OTHER = "https://other.example.com"
UNLISTED = "https://unlisted.example.com"
# Only an origin listed as is may be answered with itself: no case folding, no
# prefix or suffix matching, no default port, nothing for a missing origin.
NEAR_MISSES = (
    "",
    "null",
    f"{OTHER}/",
    f"{OTHER}:443",
    f"{OTHER}.evil.example",
    "https://evil.other.example.com",
    "https://OTHER.example.com",
    "http://other.example.com",
)
# What restricts nothing: no app url, a blank one, or one that names no origin.
NO_APP_URLS = (False, " ", "*", "null")


def set_origins(env, app_url, extra_origins=None):
    icp = env["ir.config_parameter"].sudo()
    icp.set_param("roomdoo_app_url", app_url)
    icp.set_param("roomdoo_app_extra_origins", extra_origins)


def vary(response):
    return [
        value.strip().lower()
        for value in response.headers.get("Vary", "").split(",")
        if value.strip()
    ]


def preflight_headers(origin, method="GET"):
    # What a client authenticating with a bearer token asks for.
    return {
        "Origin": origin,
        "Access-Control-Request-Method": method,
        "Access-Control-Request-Headers": "authorization, content-type",
    }


def lets_the_request_through(test, response, method="GET"):
    allowed_headers = response.headers.get("Access-Control-Allow-Headers", "")
    test.assertIn("authorization", allowed_headers.lower())
    test.assertIn(method, response.headers.get("Access-Control-Allow-Methods", ""))


class TestFastapiCorsOrigins(CommonTestPmsApi):
    """What the app answers by itself: preflights and successful requests."""

    def _client(self, app_url, extra_origins=None):
        # The origins are read when the app is built, so build it again.
        set_origins(self.env, app_url, extra_origins)
        return self._create_test_client(app=self.pms_fastapi_app._get_app())

    def assertAllows(self, response, origin):
        self.assertEqual(response.headers.get("Access-Control-Allow-Origin"), origin)
        self.assertEqual(
            response.headers.get("Access-Control-Allow-Credentials"), "true"
        )
        self.assertIn("origin", vary(response))

    def test_a_preflight_from_an_extra_origin_is_answered_with_it(self):
        with self._client(APP, OTHER) as client:
            response = client.options("/countries", headers=preflight_headers(OTHER))

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        self.assertAllows(response, OTHER)
        lets_the_request_through(self, response)

    def test_a_preflight_from_the_app_is_answered_with_it(self):
        with self._client(APP, OTHER) as client:
            response = client.options("/countries", headers=preflight_headers(APP))

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        self.assertAllows(response, APP)

    def test_a_preflight_from_any_other_origin_is_refused(self):
        with self._client(APP, OTHER) as client:
            for origin in (UNLISTED, *NEAR_MISSES):
                with self.subTest(origin=origin):
                    response = client.options(
                        "/countries", headers=preflight_headers(origin)
                    )

                    self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                    self.assertNotIn("Access-Control-Allow-Origin", response.headers)

    def test_an_answer_to_an_extra_origin_names_it(self):
        with self._client(APP, OTHER) as client:
            response = client.get("/countries", headers={"Origin": OTHER})

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        self.assertAllows(response, OTHER)

    def test_an_answer_to_any_other_origin_names_none(self):
        with self._client(APP, OTHER) as client:
            response = client.get("/countries", headers={"Origin": UNLISTED})

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)

    def test_without_extra_origins_only_the_app_is_allowed(self):
        with self._client(APP) as client:
            refused = client.options("/countries", headers=preflight_headers(OTHER))
            allowed = client.options("/countries", headers=preflight_headers(APP))

        self.assertEqual(refused.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(allowed.status_code, status.HTTP_200_OK)
        self.assertAllows(allowed, APP)

    def test_without_an_app_url_extra_origins_change_nothing(self):
        # Any origin is answered then, without credentials: browsers reject
        # a credentialed answer whose origin is "*".
        for app_url in NO_APP_URLS:
            with self.subTest(app_url=app_url), self._client(app_url, OTHER) as client:
                response = client.options(
                    "/countries", headers=preflight_headers(OTHER)
                )

                self.assertEqual(
                    response.status_code, status.HTTP_200_OK, response.text
                )
                self.assertEqual(
                    response.headers.get("Access-Control-Allow-Origin"), "*"
                )
                self.assertNotIn("Access-Control-Allow-Credentials", response.headers)


@tagged("post_install", "-at_install")
class TestFastapiCorsErrors(HttpCase):
    """The errors of the app are answered by Odoo, not by the app, so that a
    failure rolls the transaction back. Those answers never pass through the
    app's own CORS handling and have to name the origin by themselves.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.endpoint = cls.env["fastapi.endpoint"].create(
            {
                "name": "PMS FastAPI",
                "app": "pms_api",
                "root_path": "/pmsApi",
                "user_id": cls.env.ref("pms_fastapi.pms_fastapi_user").id,
            }
        )
        cls.endpoint._handle_registry_sync()
        # The routing map is cached per process: rebuild it so that it carries
        # this route, and again once the route is rolled back.
        cls.env["ir.http"]._clear_routing_map()
        cls.addClassCleanup(cls.env["ir.http"]._clear_routing_map)

    def setUp(self):
        super().setUp()
        # A request without a session only reaches a database when the server
        # has a single one.
        self.authenticate(None, None)
        set_origins(self.env, APP, OTHER)
        # Apps are built once and pooled. A parameter change reaches the pool
        # through the registry signaling, which does not run in tests, and an
        # app built here must not outlive these tests either.
        fastapi_app_pool.invalidate(self.env)
        self.addCleanup(fastapi_app_pool.invalidate, self.env)

    def _call(self, path, origin):
        return self.url_open(path, headers={"Origin": origin})

    def _preflight(self, path, origin):
        return self.opener.options(
            self.base_url() + path,
            headers=preflight_headers(origin, method="POST"),
            timeout=12,
        )

    @mute_logger("odoo.http")
    def test_an_error_for_an_extra_origin_names_it(self):
        response = self._call("/pmsApi/agencies", OTHER)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.headers.get("Access-Control-Allow-Origin"), OTHER)
        self.assertEqual(
            response.headers.get("Access-Control-Allow-Credentials"), "true"
        )
        self.assertIn("origin", vary(response))

    @mute_logger("odoo.http")
    def test_an_error_for_any_other_origin_keeps_the_app(self):
        for origin in (UNLISTED, *NEAR_MISSES):
            with self.subTest(origin=origin):
                response = self._call("/pmsApi/agencies", origin)

                self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
                self.assertEqual(
                    response.headers.get("Access-Control-Allow-Origin"), APP
                )

    def test_a_preflight_from_an_extra_origin_is_answered_with_it(self):
        response = self._preflight("/pmsApi/login", OTHER)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        self.assertEqual(response.headers.get("Access-Control-Allow-Origin"), OTHER)
        self.assertEqual(
            response.headers.get("Access-Control-Allow-Credentials"), "true"
        )
        self.assertIn("origin", vary(response))
        lets_the_request_through(self, response, method="POST")

    def test_a_preflight_from_any_other_origin_is_refused(self):
        response = self._preflight("/pmsApi/login", UNLISTED)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)
