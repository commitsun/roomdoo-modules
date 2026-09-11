# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""Master data export: the payloads Channex receives, and how many calls it
takes. The call count is asserted deliberately: Channex reviews call efficiency
during certification, and a silent regression there is invisible otherwise."""

from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from .common import GROUP_UUID, ChannexConnectorCase

PROPERTY_BINDING = "channel.channex.pms.property"
ROOM_TYPE_BINDING = "channel.channex.pms.room.type"


@tagged("post_install", "-at_install")
class TestPropertyExport(ChannexConnectorCase):
    def _bind_property(self):
        return (
            self.env[PROPERTY_BINDING]
            .with_context(connector_no_export=True)
            .create(
                {
                    "odoo_id": self.pms_property.id,
                    "backend_id": self.backend.id,
                }
            )
        )

    def test_property_payload(self):
        binding = self._bind_property()
        self.env[PROPERTY_BINDING].export_record(self.backend, self.pms_property)
        posts = self.server.calls_to("POST", "properties")
        self.assertEqual(len(posts), 1)
        payload = posts[0][2]["property"]
        self.assertEqual(payload["title"], "Channex Property")
        self.assertEqual(payload["currency"], self.company.currency_id.name)
        self.assertEqual(payload["timezone"], "Europe/Madrid")
        self.assertEqual(payload["property_type"], "hotel")
        self.assertEqual(payload["group_id"], GROUP_UUID)
        # Availability is pushed as an absolute number, so letting Channex
        # decrement on confirmation is safe and narrows the overbooking window,
        # while the other two could inflate inventory Odoo has not released.
        settings = payload["settings"]
        self.assertTrue(settings["allow_availability_autoupdate_on_confirmation"])
        self.assertFalse(settings["allow_availability_autoupdate_on_modification"])
        self.assertFalse(settings["allow_availability_autoupdate_on_cancellation"])
        self.assertEqual(settings["min_stay_type"], "both")
        binding.invalidate_recordset()
        self.assertTrue(binding.external_id)

    def test_second_export_updates_instead_of_creating(self):
        self._bind_property()
        self.env[PROPERTY_BINDING].export_record(self.backend, self.pms_property)
        self.pms_property.name = "Channex Property Renamed"
        self.env[PROPERTY_BINDING].export_record(self.backend, self.pms_property)
        self.assertEqual(len(self.server.calls_to("POST", "properties")), 1)
        self.assertEqual(len(self.server.calls_to("PUT", "properties")), 1)


@tagged("post_install", "-at_install")
class TestRoomTypeExport(ChannexConnectorCase):
    def _bind(self, model, record):
        return (
            self.env[model]
            .with_context(connector_no_export=True)
            .create({"odoo_id": record.id, "backend_id": self.backend.id})
        )

    def test_occupancy_derived_from_the_rooms_of_this_property(self):
        binding = self._bind(ROOM_TYPE_BINDING, self.room_type)
        self.assertEqual(binding.count_of_rooms, 2)
        self.assertEqual(binding.occ_adults, 2)
        self.assertEqual(binding.default_occupancy, 2)
        self.assertFalse(binding.heterogeneous_capacity)

    def test_heterogeneous_capacity_uses_the_smallest_and_is_flagged(self):
        """Any single number is wrong here, so the safe one is chosen and the
        room type is flagged: the smallest never oversells a bed a given room
        does not have, it only hides inventory."""
        self.env["pms.room"].create(
            {
                "name": "CHX-103",
                "pms_property_id": self.pms_property.id,
                "room_type_id": self.room_type.id,
                "capacity": 4,
            }
        )
        binding = self._bind(ROOM_TYPE_BINDING, self.room_type)
        self.assertEqual(binding.count_of_rooms, 3)
        self.assertEqual(binding.occ_adults, 2)
        self.assertTrue(binding.heterogeneous_capacity)

    def test_rooms_of_another_property_are_not_counted(self):
        other_property = self.env["pms.property"].create(
            {
                "name": "Channex Other",
                "company_id": self.company.id,
                "default_pricelist_id": self.pricelist.id,
                "tz": "Europe/Madrid",
            }
        )
        self.room_type.pms_property_ids = [
            (6, 0, [self.pms_property.id, other_property.id])
        ]
        self.env["pms.room"].create(
            {
                "name": "OTHER-201",
                "pms_property_id": other_property.id,
                "room_type_id": self.room_type.id,
                "capacity": 8,
            }
        )
        binding = self._bind(ROOM_TYPE_BINDING, self.room_type)
        self.assertEqual(binding.count_of_rooms, 2)
        self.assertEqual(binding.occ_adults, 2)

    def test_room_type_payload_and_property_exported_first(self):
        self._bind(PROPERTY_BINDING, self.pms_property)
        self._bind(ROOM_TYPE_BINDING, self.room_type)
        self.env[ROOM_TYPE_BINDING].export_record(self.backend, self.room_type)
        # The property has to exist on Channex before its room types.
        self.assertEqual(len(self.server.calls_to("POST", "properties")), 1)
        posts = self.server.calls_to("POST", "room_types")
        self.assertEqual(len(posts), 1)
        payload = posts[0][2]["room_type"]
        self.assertEqual(payload["title"], "RT-CHX")
        self.assertEqual(payload["count_of_rooms"], 2)
        self.assertEqual(payload["occ_adults"], 2)
        self.assertEqual(payload["default_occupancy"], 2)
        self.assertEqual(payload["room_kind"], "room")
        self.assertTrue(payload["property_id"])
        # capacity only means something for a dorm
        self.assertNotIn("capacity", payload)

    def test_excluded_room_type_class_is_never_sent(self):
        self.backend_type.room_kind_ids.excluded = True
        self._bind(PROPERTY_BINDING, self.pms_property)
        self._bind(ROOM_TYPE_BINDING, self.room_type)
        self.env[ROOM_TYPE_BINDING].export_record(self.backend, self.room_type)
        self.assertFalse(self.server.calls_to("POST", "room_types"))

    def test_default_occupancy_over_adults_is_refused_locally(self):
        binding = self._bind(ROOM_TYPE_BINDING, self.room_type)
        binding.default_occupancy = binding.occ_adults + 1
        self._bind(PROPERTY_BINDING, self.pms_property)
        with self.assertRaises(ValidationError):
            self.env[ROOM_TYPE_BINDING].export_record(self.backend, self.room_type)
        self.assertFalse(self.server.calls_to("POST", "room_types"))


@tagged("post_install", "-at_install")
class TestCallBudget(ChannexConnectorCase):
    """Channex evaluates how many calls an integration spends, so the count is
    pinned rather than left to drift."""

    def test_setup_of_one_property_and_two_room_types(self):
        second_room_type = self._make_room_type("RT-CHX-2", "RTCX2")
        self.env["pms.room"].create(
            {
                "name": "CHX-201",
                "pms_property_id": self.pms_property.id,
                "room_type_id": second_room_type.id,
                "capacity": 3,
            }
        )
        for model, record in (
            (PROPERTY_BINDING, self.pms_property),
            (ROOM_TYPE_BINDING, self.room_type),
            (ROOM_TYPE_BINDING, second_room_type),
        ):
            self.env[model].with_context(connector_no_export=True).create(
                {"odoo_id": record.id, "backend_id": self.backend.id}
            )
        self.env[ROOM_TYPE_BINDING].export_record(self.backend, self.room_type)
        self.env[ROOM_TYPE_BINDING].export_record(self.backend, second_room_type)
        # One property and two room types: three writes, no more.
        self.assertEqual(len(self.server.calls_to("POST", "properties")), 1)
        self.assertEqual(len(self.server.calls_to("POST", "room_types")), 2)

    def test_re_export_updates_and_never_duplicates(self):
        """An explicit export always pushes: deciding that nothing changed is
        the listeners' job, not the exporter's. What matters here is that the
        second export updates rather than creating a second property."""
        self.env[PROPERTY_BINDING].with_context(connector_no_export=True).create(
            {"odoo_id": self.pms_property.id, "backend_id": self.backend.id}
        )
        self.env[PROPERTY_BINDING].export_record(self.backend, self.pms_property)
        self.env[PROPERTY_BINDING].export_record(self.backend, self.pms_property)
        self.assertEqual(len(self.server.calls_to("POST", "properties")), 1)
        self.assertEqual(len(self.server.store["properties"]), 1)
