# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""Being told about bookings instead of asking.

Channex signs nothing it sends, so what the connector trusts is not the call
but the reading it makes afterwards with its own key. These tests are about the
two halves of that: what we tell Channex to call, and what we let through when
it does.
"""

from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.connector_pms_channex.components.adapter import SECRET_HEADER

from .common import ChannexConnectorCase


@tagged("post_install", "-at_install")
class TestChannexWebhook(ChannexConnectorCase):
    def setUp(self):
        super().setUp()
        self.env["channel.channex.pms.property"].export_record(
            self.backend, self.pms_property
        )
        self.env["ir.config_parameter"].sudo().set_param(
            "web.base.url", "https://pms.example.com"
        )

    def _registered(self):
        return self.server.store.get("webhooks") or []

    # -- what Channex is told ----------------------------------------------

    def test_registering_tells_channex_where_and_how_to_call(self):
        self.backend.action_register_channex_webhook()
        self.assertEqual(len(self._registered()), 1)
        webhook = self._registered()[0]
        self.assertEqual(
            webhook["callback_url"],
            f"https://pms.example.com/channex/webhook/{self.backend.id}",
        )
        self.assertEqual(
            webhook["property_id"], self.server.store["properties"][0]["id"]
        )
        self.assertEqual(webhook["event_mask"], "booking")
        self.assertTrue(webhook["is_active"])
        # The call carries no booking on purpose: what it earns is a reading of
        # our own, and an unsigned payload is not something to write to a folio.
        self.assertFalse(webhook["send_data"])
        self.assertEqual(webhook["headers"][SECRET_HEADER], self.backend.webhook_secret)
        self.assertEqual(self.backend.webhook_external_id, webhook["id"])

    def test_registering_twice_points_the_same_webhook_at_us(self):
        """Or Channex ends up calling every address this hotel ever had."""
        self.backend.action_register_channex_webhook()
        first = self.backend.webhook_external_id
        self.backend.action_register_channex_webhook()
        self.assertEqual(len(self._registered()), 1)
        self.assertEqual(self.backend.webhook_external_id, first)
        self.assertEqual(len(self.server.calls_to("PUT", "webhooks")), 1)

    def test_the_secret_is_made_once_and_kept(self):
        self.backend.action_register_channex_webhook()
        secret = self.backend.webhook_secret
        self.assertTrue(secret)
        self.backend.action_register_channex_webhook()
        self.assertEqual(self.backend.webhook_secret, secret)

    def test_an_address_channex_cannot_reach_is_refused(self):
        """A developer machine, which is where this would be tried first."""
        self.env["ir.config_parameter"].sudo().set_param(
            "web.base.url", "http://localhost:16069"
        )
        with self.assertRaises(UserError):
            self.backend.action_register_channex_webhook()
        self.assertFalse(self._registered())

    def test_nothing_is_registered_from_a_backend_with_exports_off(self):
        """Usually a copy of a live one, and pointing the real Channex at a copy
        would take the hotel's bookings with it."""
        self.backend.export_disabled = True
        with self.assertRaises(UserError):
            self.backend.action_register_channex_webhook()
        self.assertFalse(self._registered())
        self.assertFalse(self.backend.webhook_external_id)

    def test_the_secret_never_reaches_the_log(self):
        """Channex hands the webhook headers back when it answers, so the
        secret arrives nested two levels inside a body that gets logged."""
        self.backend.action_register_channex_webhook()
        logs = self.env["channel.backend.log"].search([])
        self.assertTrue(logs)
        for log in logs:
            self.assertNotIn(self.backend.webhook_secret, log.response or "")
            self.assertNotIn(self.backend.webhook_secret, log.arguments or "")

    # -- what is let through when it calls ---------------------------------

    def test_the_secret_it_was_given_is_admitted(self):
        self.backend.action_register_channex_webhook()
        self.assertTrue(
            self.backend._channex_webhook_admits(self.backend.webhook_secret)
        )

    def test_any_other_secret_is_not(self):
        self.backend.action_register_channex_webhook()
        for secret in ("", "  ", self.backend.webhook_secret + "x"):
            self.assertFalse(self.backend._channex_webhook_admits(secret))

    def test_a_backend_nobody_registered_admits_nothing(self):
        """Not even an empty secret matching an empty one: there is nobody to be
        called by yet."""
        self.assertFalse(self.backend.webhook_secret)
        self.assertFalse(self.backend._channex_webhook_admits(""))
        self.assertFalse(self.backend._channex_webhook_admits("anything"))
