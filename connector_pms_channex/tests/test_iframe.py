# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import urllib.parse

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import GROUP_UUID, ChannexConnectorCase


@tagged("post_install", "-at_install")
class TestChannexIframe(ChannexConnectorCase):
    """Channels are created and mapped inside Channex's own UI, embedded in Odoo.

    What Odoo owns is the way in: minting the single-use token and building the
    URL of the screen.
    """

    def setUp(self):
        super().setUp()
        self.env["channel.channex.pms.property"].export_record(
            self.backend, self.pms_property
        )

    def _query(self, url):
        parts = urllib.parse.urlsplit(url)
        return f"{parts.scheme}://{parts.netloc}{parts.path}", dict(
            urllib.parse.parse_qsl(parts.query)
        )

    def test_url_points_at_the_web_root_not_the_api(self):
        base, _query = self._query(self.backend.channex_iframe_url())
        self.assertEqual(base, "https://staging.channex.io/auth/exchange")

    def test_url_carries_the_session_and_the_scope(self):
        _base, query = self._query(self.backend.channex_iframe_url())
        external_id = self.server.store["properties"][0]["id"]
        self.assertEqual(query["app_mode"], "headless")
        self.assertEqual(query["redirect_to"], "/channels")
        self.assertEqual(query["property_id"], external_id)
        self.assertEqual(query["group_id"], GROUP_UUID)
        posted = self.server.calls_to("POST", "auth")[0][2]["one_time_token"]
        self.assertTrue(query["oauth_session_key"])
        self.assertEqual(posted["property_id"], external_id)
        self.assertEqual(posted["group_id"], GROUP_UUID)
        self.assertEqual(posted["username"], self.env.user.name)

    def test_every_call_mints_a_new_token(self):
        """Channex drops the token on first use, so a screen reopened from the
        breadcrumb cannot reuse the one it was opened with."""
        first = self._query(self.backend.channex_iframe_url())[1]
        second = self._query(self.backend.channex_iframe_url())[1]
        self.assertNotEqual(first["oauth_session_key"], second["oauth_session_key"])
        self.assertEqual(len(self.server.calls_to("POST", "auth")), 2)

    def test_another_page_can_be_embedded(self):
        _base, query = self._query(self.backend.channex_iframe_url(page="/messages"))
        self.assertEqual(query["redirect_to"], "/messages")

    def test_the_ui_speaks_the_language_of_the_user(self):
        self.env["res.lang"]._activate_lang("es_ES")
        self.env.user.lang = "es_ES"
        _base, query = self._query(self.backend.channex_iframe_url())
        self.assertEqual(query["lng"], "es")

    def test_a_language_channex_lacks_falls_back_to_english(self):
        self.env["res.lang"]._activate_lang("ca_ES")
        self.env.user.lang = "ca_ES"
        _base, query = self._query(self.backend.channex_iframe_url())
        self.assertEqual(query["lng"], "en")

    def test_property_not_exported_yet_says_so(self):
        binding = self.env["channel.channex.pms.property"].search(
            [("backend_id", "=", self.backend.id)]
        )
        binding.external_id = False
        with self.assertRaises(UserError):
            self.backend.channex_iframe_url()
        # Nothing was asked of Channex: the check is local.
        self.assertFalse(self.server.calls_to("POST", "auth"))

    def test_no_group_says_so(self):
        self.backend.group_id = False
        with self.assertRaises(UserError):
            self.backend.channex_iframe_url()

    def test_exports_disabled_does_not_hand_out_a_broken_url(self):
        self.backend.export_disabled = True
        with self.assertRaises(UserError):
            self.backend.channex_iframe_url()

    def test_action_opens_the_client_action(self):
        action = self.backend.action_open_channex_channels()
        self.assertEqual(action["tag"], "channex_iframe")
        self.assertEqual(action["params"]["backend_id"], self.backend.id)
        self.assertEqual(action["params"]["page"], "/channels")
        # The token is not in the action: it would be stale on the way back.
        self.assertNotIn("oauth_session_key", str(action))
