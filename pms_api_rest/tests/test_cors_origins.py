# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""A browser hands a cross-origin answer to the page only when the answer names
the page's origin, and ``Access-Control-Allow-Origin`` holds a single one. An
API serving several clients, each from its own host, therefore has to answer
every request with the origin it comes from, as long as that origin is allowed,
and keep refusing every other one exactly as before.
"""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from psycopg2.errors import InFailedSqlTransaction
from werkzeug.datastructures import Headers
from werkzeug.exceptions import Unauthorized

from odoo import http
from odoo.tests import HttpCase, TransactionCase, tagged
from odoo.tools import mute_logger

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


def vary(headers):
    return [
        value.strip().lower()
        for value in (headers.get("Vary") or "").split(",")
        if value.strip()
    ]


@contextmanager
def fake_request(env, origin, routing_type="restapi"):
    """The bits of a request the CORS headers are built from."""
    request = SimpleNamespace(
        env=env,
        httprequest=SimpleNamespace(headers=Headers({"Origin": origin})),
        dispatcher=SimpleNamespace(routing_type=routing_type),
        future_response=http.FutureResponse(),
    )
    http._request_stack.push(request)
    try:
        yield request
    finally:
        http._request_stack.pop()


@tagged("post_install", "-at_install")
class TestAllowedOrigins(TransactionCase):
    def _allowed(self, app_url, extra_origins=None):
        set_origins(self.env, app_url, extra_origins)
        return self.env["ir.http"]._pms_api_rest_allowed_origins()

    def test_without_extra_origins_only_the_app_is_allowed(self):
        self.assertEqual(self._allowed(APP), [APP])

    def test_the_app_url_is_compared_as_an_origin(self):
        # Stored URLs often end with a slash, which an origin never carries.
        self.assertEqual(self._allowed(f" {APP}/ "), [APP])

    def test_extra_origins_follow_the_app_once_each(self):
        extra = (
            f" {OTHER}/ ,https://third.example.com\n\n{OTHER},,{APP}/"
            "\thttps://fourth.example.com:8443 \n"
        )

        self.assertEqual(
            self._allowed(APP, extra),
            [
                APP,
                OTHER,
                "https://third.example.com",
                "https://fourth.example.com:8443",
            ],
        )

    def test_no_wildcard_and_no_opaque_origin_is_ever_allowed(self):
        # "*" would let in any site, with credentials, and "null" is what
        # sandboxed pages and local files send.
        self.assertEqual(self._allowed(APP, f"*, null {OTHER}"), [APP, OTHER])

    def test_without_an_app_url_nothing_is_restricted(self):
        # Taken as the app, "null" would hand the answers, credentials
        # included, to any sandboxed page.
        for app_url in (*NO_APP_URLS, " null/ "):
            with self.subTest(app_url=app_url):
                self.assertEqual(self._allowed(app_url, OTHER), [])

    def test_only_an_origin_listed_as_is_gets_its_own_answer(self):
        set_origins(self.env, APP, OTHER)
        for origin in NEAR_MISSES:
            with self.subTest(origin=origin), fake_request(self.env, origin):
                headers = dict(self.env["ir.http"]._pms_api_rest_cors_headers())

                self.assertEqual(headers["Access-Control-Allow-Origin"], APP)
        with fake_request(self.env, OTHER):
            headers = dict(self.env["ir.http"]._pms_api_rest_cors_headers())
        self.assertEqual(headers["Access-Control-Allow-Origin"], OTHER)

    def test_vary_keeps_what_the_answer_already_varied_on(self):
        set_origins(self.env, APP, OTHER)
        rule = SimpleNamespace(rule="/api/countries")
        with fake_request(self.env, OTHER) as request:
            headers = request.future_response.headers
            headers.set("Vary", "Accept-Language")

            self.env["ir.http"]._pms_api_rest_set_cors_headers(rule)

        self.assertEqual(headers.get("Access-Control-Allow-Origin"), OTHER)
        self.assertEqual(headers.get("Vary"), "Accept-Language, Origin")

    def test_an_error_keeps_its_own_vary(self):
        set_origins(self.env, APP, OTHER)
        response = http.Response(status=500, headers={"Vary": "Accept-Language"})
        with fake_request(self.env, OTHER, routing_type="fastapi"):
            self.env["ir.http"]._pms_api_rest_authorise_error(response)

        self.assertEqual(response.headers.get("Access-Control-Allow-Origin"), OTHER)
        self.assertEqual(response.headers.get("Vary"), "Accept-Language, Origin")

    def test_an_error_raised_as_is_names_the_origin_too(self):
        # A werkzeug exception builds its headers only when it is rendered.
        set_origins(self.env, APP, OTHER)
        for origin, expected in ((OTHER, OTHER), *((o, APP) for o in NEAR_MISSES)):
            with self.subTest(origin=origin):
                error = Unauthorized()
                with fake_request(self.env, origin):
                    self.env["ir.http"]._pms_api_rest_authorise_error(error)

                headers = Headers(error.get_headers())
                self.assertEqual(headers.get("Access-Control-Allow-Origin"), expected)
                self.assertEqual(
                    headers.get("Access-Control-Allow-Credentials"), "true"
                )
                self.assertEqual(vary(headers), ["origin"])

    def test_an_error_is_answered_even_when_the_origins_cannot_be_read(self):
        # What is being reported may be the very thing that broke the cursor:
        # the error must still get through, only without the permission.
        set_origins(self.env, APP, OTHER)
        response = http.Response(status=500)
        broken = InFailedSqlTransaction("current transaction is aborted")
        with fake_request(self.env, OTHER, routing_type="fastapi"), patch.object(
            type(self.env["ir.http"]), "_pms_api_rest_allowed_origins"
        ) as allowed_origins:
            allowed_origins.side_effect = broken
            self.env["ir.http"]._pms_api_rest_authorise_error(response)

        allowed_origins.assert_called_once()
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)
        self.assertNotIn("Access-Control-Allow-Credentials", response.headers)


@tagged("post_install", "-at_install")
class TestRestCorsOrigins(HttpCase):
    def setUp(self):
        super().setUp()
        # A request without a session only reaches a database when the server
        # has a single one.
        self.authenticate(None, None)
        set_origins(self.env, APP, OTHER)

    def _preflight(self, origin):
        # What a client authenticating with a bearer token asks for.
        return self.opener.options(
            self.base_url() + "/api/countries",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization, content-type",
            },
            timeout=12,
        )

    def _call(self, path, origin):
        return self.url_open(path, headers={"Origin": origin} if origin else None)

    def assertAllows(self, response, origin):
        self.assertEqual(response.headers.get("Access-Control-Allow-Origin"), origin)
        self.assertEqual(
            response.headers.get("Access-Control-Allow-Credentials"), "true"
        )

    def assertVariesOnOrigin(self, response):
        # The answer depends on the origin now: a cache must not hand the one
        # given to a page to a page of another origin.
        self.assertIn("origin", vary(response.headers))

    def assertLetsTheRequestThrough(self, response):
        allowed_headers = response.headers.get("Access-Control-Allow-Headers", "")
        self.assertIn("authorization", allowed_headers.lower())
        self.assertIn("GET", response.headers.get("Access-Control-Allow-Methods", ""))

    def test_a_preflight_from_an_extra_origin_is_answered_with_it(self):
        response = self._preflight(OTHER)

        self.assertEqual(response.status_code, 204)
        self.assertAllows(response, OTHER)
        self.assertVariesOnOrigin(response)
        self.assertLetsTheRequestThrough(response)

    def test_a_preflight_from_the_app_is_answered_with_it(self):
        response = self._preflight(APP)

        self.assertEqual(response.status_code, 204)
        self.assertAllows(response, APP)
        self.assertVariesOnOrigin(response)

    @mute_logger("odoo.addons.base_rest.http")
    def test_any_other_origin_keeps_getting_the_app(self):
        for origin in (UNLISTED, "null"):
            with self.subTest(origin=origin):
                preflight = self._preflight(origin)
                error = self._call("/api/users/current", origin)

                self.assertEqual(preflight.status_code, 204)
                self.assertGreaterEqual(error.status_code, 400)
                for response in (preflight, error):
                    self.assertAllows(response, APP)
                    self.assertVariesOnOrigin(response)
        response = self._call("/api/countries", None)
        self.assertAllows(response, APP)
        self.assertVariesOnOrigin(response)

    def test_an_answer_to_an_extra_origin_names_it(self):
        response = self._call("/api/countries", OTHER)

        self.assertEqual(response.status_code, 200)
        self.assertAllows(response, OTHER)
        self.assertVariesOnOrigin(response)

    @mute_logger("odoo.addons.base_rest.http")
    def test_an_error_for_an_extra_origin_names_it(self):
        response = self._call("/api/users/current", OTHER)

        self.assertGreaterEqual(response.status_code, 400)
        self.assertAllows(response, OTHER)
        self.assertVariesOnOrigin(response)

    @mute_logger("odoo.addons.base_rest.http")
    def test_an_error_after_the_answer_got_its_headers_names_the_origin_once(self):
        # A body that is not JSON is refused once the route has matched and
        # the headers of the answer are written; the error answer must neither
        # lose them nor repeat them. A repeated header would come back joined
        # into a single value here, which the comparison would catch.
        response = self.opener.get(
            self.base_url() + "/api/countries",
            data="{",
            headers={"Origin": OTHER, "Content-Type": "application/json"},
            timeout=12,
        )

        self.assertEqual(response.status_code, 400)
        self.assertAllows(response, OTHER)
        self.assertVariesOnOrigin(response)

    def test_without_extra_origins_only_the_app_is_answered(self):
        set_origins(self.env, APP)

        self.assertAllows(self._preflight(OTHER), APP)
        self.assertAllows(self._call("/api/countries", OTHER), APP)

    @mute_logger("odoo.addons.base_rest.http")
    def test_without_an_app_url_extra_origins_change_nothing(self):
        # Nothing is written then: Odoo's own "*" stays, without credentials,
        # and an error names no origin, as before.
        for app_url in NO_APP_URLS:
            with self.subTest(app_url=app_url):
                set_origins(self.env, app_url, OTHER)

                preflight = self._preflight(OTHER)
                answer = self._call("/api/countries", OTHER)
                error = self._call("/api/users/current", OTHER)

                self.assertEqual(preflight.status_code, 204)
                self.assertEqual(answer.status_code, 200)
                self.assertGreaterEqual(error.status_code, 400)
                for response in (preflight, answer):
                    self.assertEqual(
                        response.headers.get("Access-Control-Allow-Origin"), "*"
                    )
                self.assertNotIn("Access-Control-Allow-Origin", error.headers)
                for response in (preflight, answer, error):
                    self.assertNotIn(
                        "Access-Control-Allow-Credentials", response.headers
                    )
                    self.assertNotIn("origin", vary(response.headers))
