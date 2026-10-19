# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import GROUP_UUID, ChannexConnectorCase


@tagged("post_install", "-at_install")
class TestChannexGroupSetup(ChannexConnectorCase):
    """The group is the one piece of setup that cannot be done on Channex when
    the account is new: their UI hides the groups screen until the account has a
    property. So it has to be reachable from Odoo."""

    def setUp(self):
        super().setUp()
        self.backend.group_id = False

    def test_group_id_must_be_a_uuid(self):
        """Regression: the id was once pasted straight from the panel URL, with
        the trailing ``/edit``, and Channex only rejected it at export time."""
        with self.assertRaises(ValidationError):
            self.backend.group_id = "%s/edit" % GROUP_UUID

    def test_fetch_single_group_assigns_it(self):
        self.server.seed("groups", [{"id": GROUP_UUID, "title": "User Group"}])
        self.backend.action_fetch_channex_group()
        self.assertEqual(self.backend.group_id, GROUP_UUID)
        self.assertEqual(self.backend.group_title, "User Group")

    def test_fetch_without_groups_raises(self):
        with self.assertRaises(UserError):
            self.backend.action_fetch_channex_group()

    def test_fetch_several_groups_offers_a_choice(self):
        other = "22222222-2222-2222-2222-222222222222"
        self.server.seed(
            "groups",
            [
                {"id": GROUP_UUID, "title": "First"},
                {"id": other, "title": "Second"},
            ],
        )
        action = self.backend.action_fetch_channex_group()
        self.assertEqual(action["res_model"], "channel.channex.group.option")
        # Nothing is picked automatically: guessing which group a hotel belongs
        # to would put the property in the wrong account scope.
        self.assertFalse(self.backend.group_id)

        options = self.env["channel.channex.group.option"].search(action["domain"])
        self.assertEqual(len(options), 2)
        options.filtered(lambda o: o.title == "Second").action_use()
        self.assertEqual(self.backend.group_id, other)
        self.assertEqual(self.backend.group_title, "Second")

    def test_create_group(self):
        self.backend.group_title = "Alda Group"
        self.backend.action_create_channex_group()
        posts = self.server.calls_to("POST", "groups")
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0][2], {"group": {"title": "Alda Group"}})
        self.assertTrue(self.backend.group_id)
        self.assertEqual(self.server.store["groups"][0]["id"], self.backend.group_id)

    def test_create_group_defaults_to_the_property_name(self):
        self.backend.action_create_channex_group()
        posts = self.server.calls_to("POST", "groups")
        self.assertEqual(posts[0][2]["group"]["title"], self.pms_property.name)

    def test_create_group_is_blocked_when_exports_are_disabled(self):
        self.backend.export_disabled = True
        self.backend.group_title = "Alda Group"
        with self.assertRaises(UserError):
            self.backend.action_create_channex_group()
        self.assertFalse(self.server.calls_to("POST", "groups"))

    def test_export_without_a_group_fails_before_calling(self):
        with self.assertRaises(UserError):
            self.env["channel.channex.pms.property"].export_record(
                self.backend, self.pms_property
            )
        self.assertFalse(self.server.calls_to("POST", "properties"))
