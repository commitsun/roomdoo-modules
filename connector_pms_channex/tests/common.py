# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.tests.common import TransactionComponentCase

from .server import FakeChannexServer


class ChannexConnectorCase(TransactionComponentCase):
    """A hotel with two room types of differing capacity, and a backend pointing
    at the fake server."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "Channex Co"})
        cls.pricelist = cls.env["product.pricelist"].create(
            {"name": "Channex PL", "company_id": cls.company.id}
        )
        cls.pms_property = cls.env["pms.property"].create(
            {
                "name": "Channex Property",
                "company_id": cls.company.id,
                "default_pricelist_id": cls.pricelist.id,
                "tz": "Europe/Madrid",
            }
        )
        cls.room_type_class = cls.env["pms.room.type.class"].create(
            {"name": "RTC Channex", "default_code": "RTCH"}
        )
        cls.room_type = cls._make_room_type("RT-CHX", "RTCX")
        # Two rooms of the same capacity: the homogeneous case.
        for name, capacity in (("CHX-101", 2), ("CHX-102", 2)):
            cls.env["pms.room"].create(
                {
                    "name": name,
                    "pms_property_id": cls.pms_property.id,
                    "room_type_id": cls.room_type.id,
                    "capacity": capacity,
                }
            )
        cls.backend_type = cls.env["channel.channex.backend.type"].create(
            {
                "name": "BT Channex",
                "model_type_id": cls.env.ref(
                    "connector_pms_channex.model_channel_channex_backend_type"
                ).id,
                "group_id": "group-uuid",
                "room_kind_ids": [
                    (
                        0,
                        0,
                        {
                            "room_type_class_id": cls.room_type_class.id,
                            "room_kind": "room",
                        },
                    )
                ],
            }
        )
        cls.backend = cls.env["channel.channex.backend"].create(
            {
                "name": "Channex Backend",
                "pms_property_id": cls.pms_property.id,
                "backend_type_id": cls.backend_type.parent_id.id,
                "api_key": "test-key",
                "environment": "staging",
            }
        )

    @classmethod
    def _make_room_type(cls, name, code, capacities=None):
        # A room type delegates to product.product, so each one needs its own.
        product = cls.env["product.product"].create(
            {"name": f"{name} product", "type": "service"}
        )
        return cls.env["pms.room.type"].create(
            {
                "name": name,
                "default_code": code,
                "class_id": cls.room_type_class.id,
                "product_id": product.id,
                "pms_property_ids": [(6, 0, [cls.pms_property.id])],
            }
        )

    def setUp(self):
        super().setUp()
        self.server = FakeChannexServer().start(self)

    def _adapter(self, binding_model):
        with self.backend.work_on(binding_model) as work:
            return work.component(usage="backend.adapter")
