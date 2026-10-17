# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.tests import tagged

from odoo.addons.queue_job.tests.common import trap_jobs

from .common import ChannexConnectorCase

BINDING = "channel.channex.pms.room.type"


@tagged("post_install", "-at_install")
class TestChannexRoomTypeLifecycle(ChannexConnectorCase):
    """Creating, archiving and deleting a room type has to reach Channex.

    The rooms are what actually drive it: Channex rejects ``count_of_rooms``
    below 1, so a room type only exists there while it has rooms in the
    backend's property.
    """

    def setUp(self):
        super().setUp()
        # The property has to exist on Channex before its room types.
        self.env["channel.channex.pms.property"].export_record(
            self.backend, self.pms_property
        )

    def _bindings(self, room_type):
        return (
            self.env[BINDING]
            .with_context(active_test=False)
            .search([("odoo_id", "=", room_type.id)])
        )

    def _run_jobs(self, trap, rounds=3):
        # A sync job queues the delete jobs it decides on, and
        # ``perform_enqueued_jobs`` only runs the batch it started with.
        for _round in range(rounds):
            if not trap.enqueued_jobs:
                return
            trap.perform_enqueued_jobs()

    # -- creation ----------------------------------------------------------

    def test_new_room_type_without_rooms_is_not_sent(self):
        """Channex has no way to represent it: ``count_of_rooms`` must be >= 1."""
        with trap_jobs() as trap:
            room_type = self._make_room_type("RT-EMPTY", "RTEM")
            self._run_jobs(trap)
        self.assertFalse(self._bindings(room_type))
        self.assertFalse(self.server.calls_to("POST", "room_types"))

    def test_room_type_reaches_channex_once_it_has_a_room(self):
        room_type = self._make_room_type("RT-NEW", "RTNW")
        with trap_jobs() as trap:
            self.env["pms.room"].create(
                {
                    "name": "CHX-201",
                    "pms_property_id": self.pms_property.id,
                    "room_type_id": room_type.id,
                    "capacity": 3,
                }
            )
            self._run_jobs(trap)
        binding = self._bindings(room_type)
        self.assertEqual(len(binding), 1)
        self.assertTrue(binding.external_id)
        posts = self.server.calls_to("POST", "room_types")
        self.assertEqual(len(posts), 1)
        payload = posts[0][2]["room_type"]
        self.assertEqual(payload["title"], "RT-NEW")
        self.assertEqual(payload["count_of_rooms"], 1)
        self.assertEqual(payload["occ_adults"], 3)

    def test_room_type_of_an_excluded_class_is_never_sent(self):
        excluded_class = self.env["pms.room.type.class"].create(
            {"name": "RTC Excluded", "default_code": "RTCX"}
        )
        self.backend_type.write(
            {
                "room_kind_ids": [
                    (
                        0,
                        0,
                        {
                            "room_type_class_id": excluded_class.id,
                            "room_kind": "room",
                            "excluded": True,
                        },
                    )
                ]
            }
        )
        product = self.env["product.product"].create(
            {"name": "excluded product", "type": "service"}
        )
        room_type = self.env["pms.room.type"].create(
            {
                "name": "RT-EXCL",
                "default_code": "RTEX",
                "class_id": excluded_class.id,
                "product_id": product.id,
                "pms_property_ids": [(6, 0, [self.pms_property.id])],
            }
        )
        with trap_jobs() as trap:
            self.env["pms.room"].create(
                {
                    "name": "CHX-301",
                    "pms_property_id": self.pms_property.id,
                    "room_type_id": room_type.id,
                    "capacity": 2,
                }
            )
            self._run_jobs(trap)
        self.assertFalse(self._bindings(room_type))
        self.assertFalse(self.server.calls_to("POST", "room_types"))

    def test_a_second_room_updates_instead_of_duplicating(self):
        with trap_jobs() as trap:
            self.env[BINDING].export_record(self.backend, self.room_type)
            self.env["pms.room"].create(
                {
                    "name": "CHX-103",
                    "pms_property_id": self.pms_property.id,
                    "room_type_id": self.room_type.id,
                    "capacity": 2,
                }
            )
            self._run_jobs(trap)
        self.assertEqual(len(self.server.calls_to("POST", "room_types")), 1)
        puts = self.server.calls_to("PUT", "room_types")
        self.assertEqual(len(puts), 1)
        self.assertEqual(puts[0][2]["room_type"]["count_of_rooms"], 3)

    # -- removal -----------------------------------------------------------

    def _export_base_room_type(self):
        self.env[BINDING].export_record(self.backend, self.room_type)
        binding = self._bindings(self.room_type)
        self.assertTrue(binding.external_id)
        return binding.external_id

    def test_archiving_removes_it_from_channex(self):
        external_id = self._export_base_room_type()
        with trap_jobs() as trap:
            self.room_type.action_archive()
            self._run_jobs(trap)
        deletes = self.server.calls_to("DELETE", "room_types")
        self.assertEqual([call[1] for call in deletes], [f"room_types/{external_id}"])
        # The binding goes with it, so an unarchive creates a fresh room type
        # rather than writing to a UUID Channex no longer knows.
        self.assertFalse(self._bindings(self.room_type))

    def test_unarchiving_creates_it_again(self):
        self._export_base_room_type()
        with trap_jobs() as trap:
            self.room_type.action_archive()
            self._run_jobs(trap)
        with trap_jobs() as trap:
            self.room_type.action_unarchive()
            self._run_jobs(trap)
        self.assertEqual(len(self.server.calls_to("POST", "room_types")), 2)
        self.assertTrue(self._bindings(self.room_type).external_id)

    def test_unlinking_removes_it_from_channex(self):
        external_id = self._export_base_room_type()
        with trap_jobs() as trap:
            self.room_type.room_ids.unlink()
            self.room_type.unlink()
            self._run_jobs(trap)
        deletes = self.server.calls_to("DELETE", "room_types")
        self.assertIn(f"room_types/{external_id}", [call[1] for call in deletes])

    def test_losing_its_last_room_removes_it_from_channex(self):
        """A room type with no rooms cannot stay: ``count_of_rooms`` has a floor
        of 1, so leaving it would keep selling rooms that are gone."""
        external_id = self._export_base_room_type()
        with trap_jobs() as trap:
            self.room_type.room_ids.unlink()
            self._run_jobs(trap)
        deletes = self.server.calls_to("DELETE", "room_types")
        self.assertEqual([call[1] for call in deletes], [f"room_types/{external_id}"])
        self.assertFalse(self._bindings(self.room_type))

    def test_deleting_one_of_two_rooms_only_updates(self):
        self._export_base_room_type()
        with trap_jobs() as trap:
            self.room_type.room_ids[0].unlink()
            self._run_jobs(trap)
        self.assertFalse(self.server.calls_to("DELETE", "room_types"))
        puts = self.server.calls_to("PUT", "room_types")
        self.assertEqual(puts[-1][2]["room_type"]["count_of_rooms"], 1)

    def test_deleting_a_room_type_already_gone_from_channex_is_not_an_error(self):
        external_id = self._export_base_room_type()
        self.server.store["room_types"] = []
        with trap_jobs() as trap:
            self.room_type.action_archive()
            self._run_jobs(trap)
        self.assertEqual(
            [call[1] for call in self.server.calls_to("DELETE", "room_types")],
            [f"room_types/{external_id}"],
        )

    # -- scoping -----------------------------------------------------------

    def test_a_room_in_another_property_does_not_reach_this_backend(self):
        other_property = self.env["pms.property"].create(
            {
                "name": "Channex Other",
                "company_id": self.company.id,
                "default_pricelist_id": self.pricelist.id,
                "tz": "Europe/Madrid",
            }
        )
        room_type = self._make_room_type("RT-OTHER", "RTOT")
        room_type.pms_property_ids = [(6, 0, [other_property.id])]
        with trap_jobs() as trap:
            self.env["pms.room"].create(
                {
                    "name": "OTH-101",
                    "pms_property_id": other_property.id,
                    "room_type_id": room_type.id,
                    "capacity": 2,
                }
            )
            self._run_jobs(trap)
        self.assertFalse(self._bindings(room_type))
        self.assertFalse(self.server.calls_to("POST", "room_types"))
