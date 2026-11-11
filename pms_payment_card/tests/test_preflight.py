# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""The browser asks before sending the card password from another origin.

If the answer to that preflight does not list ``X-Card-Password``, or the
method about to be used, the browser never sends the request at all.
"""

from odoo.tests import HttpCase, tagged

ORIGIN = "https://front.example"


@tagged("post_install", "-at_install")
class TestPaymentCardPreflight(HttpCase):
    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_param("roomdoo_app_url", ORIGIN)

    def _preflight(self, path, method):
        return self.opener.request(
            "OPTIONS",
            self.base_url() + path,
            headers={
                "Origin": ORIGIN,
                "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": "authorization, x-card-password",
            },
            timeout=10,
        )

    def _allowed(self, response, header):
        return {
            value.strip().upper()
            for value in response.headers.get(header, "").split(",")
            if value.strip()
        }

    def test_the_card_password_header_may_be_sent(self):
        for path, method in (
            ("/api/folios/1/payment-card/details", "GET"),
            ("/api/folios/1/payment-card", "PATCH"),
            ("/api/folios/1/payment-card", "DELETE"),
        ):
            response = self._preflight(path, method)

            self.assertLess(response.status_code, 300, path)
            self.assertIn(
                "X-CARD-PASSWORD",
                self._allowed(response, "Access-Control-Allow-Headers"),
            )
            self.assertIn(
                "AUTHORIZATION", self._allowed(response, "Access-Control-Allow-Headers")
            )
            self.assertEqual(
                response.headers.get("Access-Control-Allow-Origin"), ORIGIN
            )

    def test_every_method_of_the_path_is_allowed(self):
        response = self._preflight("/api/folios/1/payment-card", "PATCH")

        self.assertTrue(
            {"GET", "POST", "PATCH", "DELETE"}
            <= self._allowed(response, "Access-Control-Allow-Methods")
        )
