# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""Two channel managers in the same instance and in the same hotel.

This is the requirement the whole generic layer exists for, so it is checked
against a second connector rather than only against the one already in
production: a hotel selling through two channel managers at once must keep the
two sides independent, and the generic layer must route each call to the right
one.
"""

from odoo.tests.common import tagged

from odoo.addons.component.tests.common import TransactionComponentCase
from odoo.addons.connector_pms_dummy.components import adapter as dummy_adapter

WUBOOK_INSTALLED = True
try:
    from odoo.addons.connector_pms_wubook.tests.test_master_sync import (  # noqa: F401
        _make_backend_environment,
    )
except ImportError:  # pragma: no cover
    WUBOOK_INSTALLED = False


@tagged("post_install", "-at_install")
class TestTwoChannelManagers(TransactionComponentCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "Coexistence Co"})
        cls.pricelist = cls.env["product.pricelist"].create(
            {"name": "Coexistence PL", "company_id": cls.company.id}
        )
        cls.pms_property = cls.env["pms.property"].create(
            {
                "name": "Coexistence Property",
                "company_id": cls.company.id,
                "default_pricelist_id": cls.pricelist.id,
            }
        )
        cls.other_property = cls.env["pms.property"].create(
            {
                "name": "Coexistence Other Property",
                "company_id": cls.company.id,
                "default_pricelist_id": cls.pricelist.id,
            }
        )
        cls.room_type_class = cls.env["pms.room.type.class"].create(
            {"name": "RTC Coex", "default_code": "RTCC"}
        )
        cls.room_type = cls.env["pms.room.type"].create(
            {
                "name": "RT-COEX",
                "default_code": "RTCX",
                "class_id": cls.room_type_class.id,
                "product_id": cls.env["product.product"]
                .create({"name": "RT-COEX product", "type": "service"})
                .id,
                "pms_property_ids": [(6, 0, [cls.pms_property.id])],
            }
        )
        cls.backend = cls._make_dummy_backend(cls.pms_property, "Dummy A")
        cls.other_backend = cls._make_dummy_backend(cls.other_property, "Dummy B")

    @classmethod
    def _make_dummy_backend(cls, pms_property, name):
        backend_type = cls.env["channel.dummy.backend.type"].create(
            {
                "name": f"BT {name}",
                "model_type_id": cls.env.ref(
                    "connector_pms_dummy.model_channel_dummy_backend_type"
                ).id,
            }
        )
        return cls.env["channel.dummy.backend"].create(
            {
                "name": name,
                "pms_property_id": pms_property.id,
                "backend_type_id": backend_type.parent_id.id,
            }
        )

    def setUp(self):
        super().setUp()
        dummy_adapter.reset()
        self.addCleanup(dummy_adapter.reset)

    # -- registry ----------------------------------------------------------

    def test_registry_discovers_both_connectors(self):
        fields_ = self.env["channel.backend"]._channel_binding_field_names(
            "pms.room.type"
        )
        self.assertIn("channel_dummy_bind_ids", fields_)
        if WUBOOK_INSTALLED:
            self.assertIn("channel_wubook_bind_ids", fields_)

    def test_binding_model_resolved_per_backend(self):
        self.assertEqual(
            self.backend.parent_id._channel_binding_model("pms.room.type"),
            "channel.dummy.pms.room.type",
        )

    # -- component resolution ---------------------------------------------

    def test_each_backend_resolves_its_own_components(self):
        with self.backend.work_on("channel.dummy.pms.room.type") as work:
            adapter = work.component(usage="backend.adapter")
        self.assertEqual(adapter._name, "channel.dummy.pms.room.type.adapter")

    def test_generic_components_are_served_to_this_collection(self):
        """The candidates component has no _collection, so it must be reachable
        from a connector that never declared one of its own."""
        with self.backend.work_on("channel.dummy.pms.room.type") as work:
            candidates = work.component(usage="connect.candidates")
        self.assertEqual(candidates._name, "channel.connect.candidates")

    def test_calls_go_to_the_backend_that_issued_them(self):
        dummy_adapter.seed(self.backend, "room_types", [{"id": "rt-1", "name": "One"}])
        with self.backend.work_on("channel.dummy.pms.room.type") as work:
            work.component(usage="backend.adapter").search_read([])
        self.assertEqual(len(dummy_adapter.calls_for(self.backend, "room_types")), 1)
        self.assertFalse(dummy_adapter.calls_for(self.other_backend))

    # -- connection state across connectors --------------------------------

    def test_state_and_backends_cover_every_connector(self):
        self.env["channel.dummy.pms.room.type"].create(
            {
                "odoo_id": self.room_type.id,
                "backend_id": self.backend.id,
                "external_id": "rt-dummy",
            }
        )
        self.assertEqual(self.room_type.channel_connection_state, "connected")
        self.assertEqual(self.room_type.channel_backend_ids, self.backend.parent_id)

    def test_bindings_of_both_connectors_are_listed(self):
        if not WUBOOK_INSTALLED:
            self.skipTest("connector_pms_wubook is not installed")
        wubook_backend = self._make_wubook_backend()
        self.env["channel.dummy.pms.room.type"].create(
            {
                "odoo_id": self.room_type.id,
                "backend_id": self.backend.id,
                "external_id": "rt-dummy",
            }
        )
        self.env["channel.wubook.pms.room.type"].create(
            {
                "odoo_id": self.room_type.id,
                "backend_id": wubook_backend.id,
                "external_id": 4242,
            }
        )
        bindings = self.room_type._channel_bindings()
        # One recordset per connector: they are different models and cannot be
        # merged into a single one.
        self.assertEqual(len(bindings), 2)
        self.assertEqual(
            self.room_type.channel_backend_ids,
            self.backend.parent_id | wubook_backend.parent_id,
        )

    def test_same_hotel_two_connectors_keep_separate_bindings(self):
        """The unique constraint is per (backend, record), so the same room type
        can be bound once per channel manager in the same hotel."""
        if not WUBOOK_INSTALLED:
            self.skipTest("connector_pms_wubook is not installed")
        wubook_backend = self._make_wubook_backend()
        dummy_binding = self.env["channel.dummy.pms.room.type"].create(
            {
                "odoo_id": self.room_type.id,
                "backend_id": self.backend.id,
                "external_id": "rt-dummy",
            }
        )
        wubook_binding = self.env["channel.wubook.pms.room.type"].create(
            {
                "odoo_id": self.room_type.id,
                "backend_id": wubook_backend.id,
                "external_id": 4242,
            }
        )
        self.assertEqual(dummy_binding.odoo_id, wubook_binding.odoo_id)
        self.assertNotEqual(dummy_binding.backend_id._name, wubook_binding._name)

    def _make_wubook_backend(self):
        backend_type = self.env["channel.wubook.backend.type"].create(
            {
                "name": "BT Coex Wubook",
                "model_type_id": self.env.ref(
                    "connector_pms_wubook.model_channel_wubook_backend_type"
                ).id,
                "direct_channel_type_id": self.env.ref(
                    "connector_pms_wubook.main_pms_sale_channel_wubook"
                ).id,
            }
        )
        payment_method_line = self.env["account.payment.method.line"].search(
            [], limit=1
        )
        return self.env["channel.wubook.backend"].create(
            {
                "name": "Wubook Coex",
                "pms_property_id": self.pms_property.id,
                "backend_type_id": backend_type.parent_id.id,
                "username": "X",
                "password": "X",
                "property_code": "X",
                "pkey": "X",
                "pricelist_external_id": 1,
                "wubook_payment_method_line_id": payment_method_line.id,
            }
        )

    # -- wizard ------------------------------------------------------------

    def test_wizard_offers_every_connector_of_the_property(self):
        if not WUBOOK_INSTALLED:
            self.skipTest("connector_pms_wubook is not installed")
        wubook_backend = self._make_wubook_backend()
        candidates = self.room_type._channel_candidate_backends()
        self.assertEqual(candidates, self.backend.parent_id | wubook_backend.parent_id)

    def test_wizard_never_offers_another_hotels_backend(self):
        self.assertNotIn(
            self.other_backend.parent_id,
            self.room_type._channel_candidate_backends(),
        )

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
        self.assertEqual(wizard.binding_model, "channel.dummy.pms.room.type")
        wizard.action_connect()
        binding = self.env["channel.dummy.pms.room.type"].search(
            [("odoo_id", "=", self.room_type.id)]
        )
        self.assertEqual(len(binding), 1)
        # A Char external id survives as text; Wubook's Integer one is cast.
        self.assertEqual(binding.external_id, "rt-manual")

    def test_wizard_lists_candidates_through_the_generic_component(self):
        dummy_adapter.seed(
            self.backend,
            "room_types",
            [{"id": "rt-1", "name": "Free one"}, {"id": "rt-2", "name": "Taken"}],
        )
        self.env["channel.dummy.pms.room.type"].create(
            {
                "odoo_id": self.room_type.id,
                "backend_id": self.backend.id,
                "external_id": "rt-2",
            }
        )
        other_room_type = self.env["pms.room.type"].create(
            {
                "name": "RT-COEX-2",
                "default_code": "RTCX2",
                "class_id": self.room_type_class.id,
                "product_id": self.env["product.product"]
                .create({"name": "RT-COEX-2 product", "type": "service"})
                .id,
                "pms_property_ids": [(6, 0, [self.pms_property.id])],
            }
        )
        wizard = self.env["channel.connect.wizard"].create(
            {
                "res_model": "pms.room.type",
                "res_id": other_room_type.id,
                "backend_id": self.backend.parent_id.id,
                "mode": "existing",
            }
        )
        wizard.reload_candidates()
        external_ids = wizard.candidate_ids.mapped("external_id")
        self.assertIn("rt-1", external_ids)
        # Already bound on this backend, so not offered again.
        self.assertNotIn("rt-2", external_ids)
        self.assertEqual(
            wizard.candidate_ids.filtered(lambda c: c.external_id == "rt-1").name,
            "Free one [#rt-1]",
        )
