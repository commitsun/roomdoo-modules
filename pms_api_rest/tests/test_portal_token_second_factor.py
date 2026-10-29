# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""The app hands out links that open a page of this backend without asking the
user to log in again, carrying a single use token instead.

The token answers for both factors, so it is the only thing this route takes:
a password opens nothing here, and a token that nobody followed in time stops
working.
"""
import base64
import json
import os

from odoo.tests import HttpCase, tagged

from odoo.addons.pms_api_rest.models.res_users import ONE_TIME_KEY_MAX_AGE


@tagged("post_install", "-at_install")
class TestPortalTokenSecondFactor(HttpCase):
    def setUp(self):
        super().setUp()
        self.link_user = self.env["res.users"].create(
            {
                "name": "Link Follower",
                "login": "link.follower@example.org",
                "password": "supersecret",
            }
        )

    def _mint_a_token(self):
        return (
            self.env["one.time.res.users.apikeys"]
            .with_user(self.link_user)
            ._generate("ots", "test")
        )

    def _follow(self, token):
        return self.url_open(
            f"/login_portal_token?login={self.link_user.login}&token={token}",
            allow_redirects=False,
        )

    def _follow_a_link(self):
        return self._follow(self._mint_a_token())

    def _session_uid(self):
        """Who the session belongs to, or nothing when there is no session.

        The route answers to logged in callers only: everyone else gets either
        the login page or an error, so the absence of an answer is the absence
        of a session.
        """
        response = self.url_open(
            "/web/session/get_session_info",
            data=json.dumps({"jsonrpc": "2.0", "method": "call", "params": {}}),
            headers={"Content-Type": "application/json"},
        )
        if not response.headers.get("Content-Type", "").startswith("application/json"):
            return None
        return response.json().get("result", {}).get("uid")

    def test_a_link_opens_a_session_for_a_protected_account(self):
        self.link_user.sudo().totp_secret = base64.b32encode(os.urandom(20)).decode()

        self._follow_a_link()

        self.assertEqual(self._session_uid(), self.link_user.id)

    def test_a_link_still_opens_a_session_for_an_unprotected_one(self):
        self._follow_a_link()

        self.assertEqual(self._session_uid(), self.link_user.id)

    def test_a_password_opens_nothing_here(self):
        """This route walks past the second factor, so it takes tokens only."""
        self.link_user.sudo().totp_secret = base64.b32encode(os.urandom(20)).decode()

        self._follow("supersecret")

        self.assertFalse(self._session_uid())

    def test_a_link_left_unopened_stops_working(self):
        token = self._mint_a_token()
        self.env.cr.execute(
            """
            UPDATE one_time_res_users_apikeys
            SET create_date = create_date - INTERVAL '%s SECONDS'
            """,
            [ONE_TIME_KEY_MAX_AGE + 60],
        )

        self._follow(token)

        self.assertFalse(self._session_uid())
