# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""The binding registry that lets the generic layer work with any channel
manager, exercised through the only connector currently installed."""

from odoo.tests.common import tagged

from odoo.addons.component.tests.common import TransactionComponentCase

from .test_master_sync import _make_backend_environment

WUBOOK_BOUND_MODELS = (
    "pms.availability",
    "pms.availability.plan",
    "pms.availability.plan.rule",
    "pms.board.service",
    "pms.folio",
    "pms.property",
    "pms.room.type",
    "pms.room.type.class",
    "product.pricelist",
    "product.pricelist.item",
)


@tagged("post_install", "-at_install")
class TestBindingRegistry(TransactionComponentCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _make_backend_environment(cls)
        cls.Backend = cls.env["channel.backend"]

    def _fields_of(self, model_name):
        return self.Backend._channel_binding_field_names(model_name)

    def test_every_bound_pms_model_is_discovered(self):
        # Containment, not equality: another connector installed alongside adds
        # its own binding field to the very same models.
        for model_name in WUBOOK_BOUND_MODELS:
            with self.subTest(model=model_name):
                self.assertIn("channel_wubook_bind_ids", self._fields_of(model_name))

    def test_model_without_binding_is_empty(self):
        self.assertEqual(self._fields_of("res.partner"), ())

    def test_binding_does_not_discover_itself(self):
        """A binding delegates to its PMS model, so it carries that model's
        binding One2many and would list itself without the guard."""
        self.assertIn(
            "channel_wubook_bind_ids",
            self.env["channel.wubook.pms.availability"]._fields,
        )
        self.assertEqual(self._fields_of("channel.wubook.pms.availability"), ())

    def test_binding_with_a_child_binding_one2many_is_empty(self):
        """The plan binding has both the delegated ``channel_wubook_bind_ids``
        and its own ``channel_wubook_rule_ids`` pointing at another binding."""
        plan_binding_fields = self.env["channel.wubook.pms.availability.plan"]._fields
        self.assertIn("channel_wubook_rule_ids", plan_binding_fields)
        self.assertEqual(self._fields_of("channel.wubook.pms.availability.plan"), ())

    def test_binding_model_resolved_per_channel_manager(self):
        backend = self.backend.parent_id
        self.assertEqual(
            backend._channel_binding_model("pms.room.type"),
            "channel.wubook.pms.room.type",
        )
        self.assertEqual(
            backend._channel_binding_model("product.pricelist"),
            "channel.wubook.product.pricelist",
        )

    def test_binding_model_false_when_not_connectable(self):
        self.assertFalse(self.backend.parent_id._channel_binding_model("res.partner"))


@tagged("post_install", "-at_install")
class TestConnectionStateInvalidation(TransactionComponentCase):
    """The connection state depends on One2many fields that only exist once the
    connectors are installed, so its ``@api.depends`` is a callable resolved at
    registry setup. If that wiring broke, the state would go stale instead of
    recomputing."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _make_backend_environment(cls)

    def test_state_recomputes_without_explicit_invalidation(self):
        self.assertEqual(self.room_type_a.channel_connection_state, "disconnected")
        self.env["channel.wubook.pms.room.type"].create(
            {
                "odoo_id": self.room_type_a.id,
                "backend_id": self.backend.id,
                "external_id": 8123,
            }
        )
        self.assertEqual(self.room_type_a.channel_connection_state, "connected")

    def test_connected_backends_are_the_generic_ones(self):
        self.env["channel.wubook.pms.room.type"].create(
            {
                "odoo_id": self.room_type_a.id,
                "backend_id": self.backend.id,
                "external_id": 8124,
            }
        )
        self.assertEqual(self.room_type_a.channel_backend_ids, self.backend.parent_id)

    def test_state_goes_back_to_disconnected_on_unlink(self):
        binding = self.env["channel.wubook.pms.room.type"].create(
            {
                "odoo_id": self.room_type_a.id,
                "backend_id": self.backend.id,
                "external_id": 8125,
            }
        )
        self.assertEqual(self.room_type_a.channel_connection_state, "connected")
        binding.unlink()
        self.assertEqual(self.room_type_a.channel_connection_state, "disconnected")
