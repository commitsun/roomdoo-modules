# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""A browser hands a cross-origin answer to the page only when the answer says
that origin may read it. Error answers of this API are built away from the
place that grants it, so without help they reach the client stripped of it and
are thrown away before it can look at them: a rejected password becomes
indistinguishable from an unreachable server.
"""
from odoo.tests import HttpCase, tagged

ORIGIN = "https://client.example"


@tagged("post_install", "-at_install")
class TestErrorCors(HttpCase):
    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_param("roomdoo_app_url", ORIGIN)

    def _call(self, path):
        return self.url_open(path, headers={"Origin": ORIGIN})

    def test_an_error_says_who_may_read_it(self):
        response = self._call("/api/users/current")

        self.assertGreaterEqual(response.status_code, 400)
        self.assertEqual(response.headers.get("Access-Control-Allow-Origin"), ORIGIN)
        self.assertEqual(
            response.headers.get("Access-Control-Allow-Credentials"), "true"
        )

    def test_a_successful_answer_keeps_saying_it_too(self):
        response = self._call("/api/countries")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("Access-Control-Allow-Origin"), ORIGIN)
