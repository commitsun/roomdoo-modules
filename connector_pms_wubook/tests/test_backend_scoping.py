# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""Backend selection must be scoped to the record's property.

With one backend per hotel, resolving "the" backend with
``search([], order="id", limit=1)`` always returned the lowest id in the
database, so connecting a room type of hotel B offered a backend of hotel A.
"""

from odoo.exceptions import UserError
from odoo.tests.common import tagged
from odoo.tools import mute_logger

from odoo.addons.component.tests.common import TransactionComponentCase

from .test_master_sync import _make_backend_environment


@tagged("post_install", "-at_install")
class TestConnectBackendScoping(TransactionComponentCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _make_backend_environment(cls)
        # A second hotel with its own backend, sharing the backend type.
        cls.other_property = cls.env["pms.property"].create(
            {
                "name": "Other Scoping Property",
                "company_id": cls.company.id,
                "default_pricelist_id": cls.pricelist_default.id,
            }
        )
        cls.other_backend = cls.env["channel.wubook.backend"].create(
            {
                "name": "Other Scoping Backend",
                "pms_property_id": cls.other_property.id,
                "backend_type_id": cls.backend.backend_type_id.id,
                "username": "X",
                "password": "X",
                "property_code": "Y",
                "pkey": "X",
                "pricelist_external_id": 1,
                "wubook_payment_method_line_id": (
                    cls.backend.wubook_payment_method_line_id.id
                ),
            }
        )
        # A room type delegates to ``product.product`` (``_inherits``), so
        # ``pms_property_ids`` lives on the product: each room type needs its
        # own, or they would share (and overwrite) each other's properties.
        cls.other_room_type = cls.env["pms.room.type"].create(
            {
                "name": "RT-OTHER",
                "default_code": "RTOTH",
                "class_id": cls.room_type_class.id,
                "product_id": cls.env["product.product"]
                .create(
                    {
                        "name": "RT-OTHER product",
                        "type": "service",
                        "list_price": 100.0,
                    }
                )
                .id,
                "pms_property_ids": [(6, 0, [cls.other_property.id])],
            }
        )

    def test_candidate_backends_scoped_to_own_property(self):
        self.assertEqual(
            self.room_type_a._channel_candidate_backends(), self.backend.parent_id
        )
        self.assertEqual(
            self.other_room_type._channel_candidate_backends(),
            self.other_backend.parent_id,
        )

    def test_global_record_sees_every_backend(self):
        """A pricelist with no property is global, so any backend applies."""
        self.pricelist_default.pms_property_ids = [(5, 0, 0)]
        candidates = self.pricelist_default._channel_candidate_backends()
        self.assertIn(self.backend.parent_id, candidates)
        self.assertIn(self.other_backend.parent_id, candidates)

    @mute_logger(
        "odoo.addons.connector_pms.wizards.wizard_connect",
        "odoo.addons.connector_pms.models.common.channel_connect_mixin",
    )
    def test_wizard_never_defaults_to_another_hotels_backend(self):
        action = self.other_room_type.action_open_channel_connect_wizard()
        wizard = self.env["channel.connect.wizard"].browse(action["res_id"])
        self.assertEqual(wizard.backend_id, self.other_backend.parent_id)

    def test_wizard_refuses_when_no_backend_for_the_property(self):
        orphan_property = self.env["pms.property"].create(
            {
                "name": "Orphan Property",
                "company_id": self.company.id,
                "default_pricelist_id": self.pricelist_default.id,
            }
        )
        orphan_room_type = self.env["pms.room.type"].create(
            {
                "name": "RT-ORPHAN",
                "default_code": "RTORP",
                "class_id": self.room_type_class.id,
                "product_id": self.env["product.product"]
                .create(
                    {
                        "name": "RT-ORPHAN product",
                        "type": "service",
                        "list_price": 100.0,
                    }
                )
                .id,
                "pms_property_ids": [(6, 0, [orphan_property.id])],
            }
        )
        with self.assertRaises(UserError):
            orphan_room_type.action_open_channel_connect_wizard()
