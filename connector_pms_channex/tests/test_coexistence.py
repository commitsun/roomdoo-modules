# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""Two channel managers in the same instance and in the same hotel.

This is the requirement the whole generic layer exists for, and it cannot be
checked from one connector alone. It lives here rather than in
``connector_pms`` because it needs two concrete connectors installed, and
Channex is the second one to arrive; it skips when Wubook is absent.

Between them the two connectors cover the fallback in both directions, which is
why no third, fake connector is needed: Channex overrides ``connect.candidates``
and inherits the generic ``connect.hooks``, and Wubook does the opposite.
"""

from odoo.tests.common import tagged
from odoo.tools import mute_logger

from .common import ChannexConnectorCase

WUBOOK_INSTALLED = True
try:
    from odoo.addons.connector_pms_wubook.tests.test_master_sync import (  # noqa: F401
        _make_backend_environment,
    )
except ImportError:  # pragma: no cover
    WUBOOK_INSTALLED = False

CHANNEX_ROOM_TYPE_BINDING = "channel.channex.pms.room.type"
WUBOOK_ROOM_TYPE_BINDING = "channel.wubook.pms.room.type"


@tagged("post_install", "-at_install")
class TestTwoChannelManagers(ChannexConnectorCase):
    """A Channex backend and a Wubook backend on the same property."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not WUBOOK_INSTALLED:
            return
        cls.wubook_backend = cls._make_wubook_backend(cls.pms_property, "Wubook Coex")
        cls.other_property = cls.env["pms.property"].create(
            {
                "name": "Coexistence Other Property",
                "company_id": cls.company.id,
                "default_pricelist_id": cls.pricelist.id,
            }
        )
        cls.other_wubook_backend = cls._make_wubook_backend(
            cls.other_property, "Wubook Other Hotel"
        )

    @classmethod
    def _make_wubook_backend(cls, pms_property, name):
        backend_type = cls.env["channel.wubook.backend.type"].create(
            {
                "name": f"BT {name}",
                "model_type_id": cls.env.ref(
                    "connector_pms_wubook.model_channel_wubook_backend_type"
                ).id,
                "direct_channel_type_id": cls.env.ref(
                    "connector_pms_wubook.main_pms_sale_channel_wubook"
                ).id,
            }
        )
        payment_method_line = cls.env["account.payment.method.line"].search([], limit=1)
        return cls.env["channel.wubook.backend"].create(
            {
                "name": name,
                "pms_property_id": pms_property.id,
                "backend_type_id": backend_type.parent_id.id,
                "username": "X",
                "password": "X",
                "property_code": "X",
                "pkey": "X",
                "pricelist_external_id": 1,
                "wubook_payment_method_line_id": payment_method_line.id,
            }
        )

    def setUp(self):
        super().setUp()
        if not WUBOOK_INSTALLED:
            self.skipTest("connector_pms_wubook is not installed")

    def _bind_channex(self, record, external_id):
        return (
            self.env[CHANNEX_ROOM_TYPE_BINDING]
            .with_context(connector_no_export=True)
            .create(
                {
                    "odoo_id": record.id,
                    "backend_id": self.backend.id,
                    "external_id": external_id,
                }
            )
        )

    def _bind_wubook(self, record, external_id):
        return (
            self.env[WUBOOK_ROOM_TYPE_BINDING]
            .with_context(connector_no_export=True)
            .create(
                {
                    "odoo_id": record.id,
                    "backend_id": self.wubook_backend.id,
                    "external_id": external_id,
                }
            )
        )

    # -- registry ----------------------------------------------------------

    def test_registry_discovers_both_connectors(self):
        fields_ = self.env["channel.backend"]._channel_binding_field_names(
            "pms.room.type"
        )
        self.assertIn("channel_channex_bind_ids", fields_)
        self.assertIn("channel_wubook_bind_ids", fields_)

    def test_binding_model_resolved_per_connector(self):
        self.assertEqual(
            self.backend.parent_id._channel_binding_model("pms.room.type"),
            CHANNEX_ROOM_TYPE_BINDING,
        )
        self.assertEqual(
            self.wubook_backend.parent_id._channel_binding_model("pms.room.type"),
            WUBOOK_ROOM_TYPE_BINDING,
        )

    # -- component resolution ----------------------------------------------

    def test_each_backend_resolves_its_own_adapter(self):
        with self.backend.work_on(CHANNEX_ROOM_TYPE_BINDING) as work:
            channex_adapter = work.component(usage="backend.adapter")
        with self.wubook_backend.work_on(WUBOOK_ROOM_TYPE_BINDING) as work:
            wubook_adapter = work.component(usage="backend.adapter")
        self.assertNotEqual(channex_adapter._name, wubook_adapter._name)
        self.assertEqual(channex_adapter._name, "channel.channex.pms.room.type.adapter")

    def test_vendor_component_wins_where_it_is_declared(self):
        """Both usages are overridden by exactly one of the two connectors."""
        with self.backend.work_on(CHANNEX_ROOM_TYPE_BINDING) as work:
            self.assertEqual(
                work.component(usage="connect.candidates")._name,
                "channel.channex.connect.candidates",
            )
        with self.wubook_backend.work_on(WUBOOK_ROOM_TYPE_BINDING) as work:
            self.assertEqual(
                work.component(usage="connect.hooks")._name,
                "channel.wubook.connect.hooks",
            )

    def test_generic_component_serves_the_connector_that_does_not_override_it(self):
        """The generic components carry no ``_collection``, so they have to
        reach a connector that never declared one of their usage. Checked in
        both directions: Channex has no hooks of its own, Wubook no candidates.
        """
        with self.backend.work_on(CHANNEX_ROOM_TYPE_BINDING) as work:
            self.assertEqual(
                work.component(usage="connect.hooks")._name, "channel.connect.hooks"
            )
        with self.wubook_backend.work_on(WUBOOK_ROOM_TYPE_BINDING) as work:
            self.assertEqual(
                work.component(usage="connect.candidates")._name,
                "channel.connect.candidates",
            )

    # -- bindings in the same hotel ----------------------------------------

    def test_same_hotel_two_connectors_keep_separate_bindings(self):
        """The unique constraint is per (backend, record), so the same room type
        can be bound once per channel manager in the same hotel."""
        channex_binding = self._bind_channex(self.room_type, "rt-channex")
        wubook_binding = self._bind_wubook(self.room_type, 4242)
        self.assertEqual(channex_binding.odoo_id, wubook_binding.odoo_id)
        self.assertNotEqual(channex_binding._name, wubook_binding._name)
        self.assertEqual(
            channex_binding.backend_id.pms_property_id,
            wubook_binding.backend_id.pms_property_id,
        )

    def test_bindings_of_both_connectors_are_listed(self):
        self._bind_channex(self.room_type, "rt-channex")
        self._bind_wubook(self.room_type, 4242)
        bindings = self.room_type._channel_bindings()
        # One recordset per connector: they are different models and cannot be
        # merged into a single one.
        self.assertEqual(len(bindings), 2)
        self.assertEqual(
            {recordset._name for recordset in bindings},
            {CHANNEX_ROOM_TYPE_BINDING, WUBOOK_ROOM_TYPE_BINDING},
        )

    def test_connection_state_and_backends_cover_every_connector(self):
        self._bind_channex(self.room_type, "rt-channex")
        self.room_type.invalidate_recordset()
        self.assertEqual(self.room_type.channel_connection_state, "connected")
        self.assertEqual(self.room_type.channel_backend_ids, self.backend.parent_id)

        self._bind_wubook(self.room_type, 4242)
        self.room_type.invalidate_recordset()
        self.assertEqual(
            self.room_type.channel_backend_ids,
            self.backend.parent_id | self.wubook_backend.parent_id,
        )

    # -- wizard ------------------------------------------------------------

    def test_wizard_offers_every_connector_of_the_property(self):
        self.assertEqual(
            self.room_type._channel_candidate_backends(),
            self.backend.parent_id | self.wubook_backend.parent_id,
        )

    def test_wizard_never_offers_another_hotels_backend(self):
        self.assertNotIn(
            self.other_wubook_backend.parent_id,
            self.room_type._channel_candidate_backends(),
        )

    @mute_logger("odoo.addons.connector_pms.wizards.wizard_connect")
    def test_wizard_binds_through_the_chosen_connector(self):
        wizard = self.env["channel.connect.wizard"].create(
            {
                "res_model": "pms.room.type",
                "res_id": self.room_type.id,
                "backend_id": self.backend.parent_id.id,
                "mode": "manual",
                "manual_external_id": "rt-manual",
            }
        )
        self.assertEqual(wizard.binding_model, CHANNEX_ROOM_TYPE_BINDING)
        wizard.action_connect()
        binding = self.env[CHANNEX_ROOM_TYPE_BINDING].search(
            [("odoo_id", "=", self.room_type.id)]
        )
        self.assertEqual(len(binding), 1)
        # Channex external ids are UUIDs, so the Char flavour survives as text;
        # Wubook's Integer one is cast on the way in.
        self.assertEqual(binding.external_id, "rt-manual")
        self.assertFalse(
            self.env[WUBOOK_ROOM_TYPE_BINDING].search(
                [("odoo_id", "=", self.room_type.id)]
            )
        )
