# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""Tests for the occupancy of a room type on both sides of Wubook.

Wubook counts the children in the occupancy of a room, so the places only
children can take go out with it, or channels could not sell them. And a
booking that does not say who stays is the one the price was given for: the
default occupancy of the room type.
"""

import functools
from types import SimpleNamespace
from unittest import mock

from odoo.addons.component.tests.common import TransactionComponentCase
from odoo.addons.connector_pms_wubook.models.pms_folio.adapter import (
    ChannelWubookPmsFolioAdapter,
)
from odoo.addons.queue_job.tests.common import trap_jobs

from .test_master_sync import _make_backend_environment
from .test_ota_locator import _booking, _NoOtaMapping

GET_ROOM_TYPE = (
    "odoo.addons.connector_pms_wubook.models.pms_folio.adapter.get_room_type"
)


class TestRoomTypeOccupancy(TransactionComponentCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _make_backend_environment(cls)
        cls.binding = cls.env["channel.wubook.pms.room.type"].create(
            {
                "odoo_id": cls.room_type_a.id,
                "backend_id": cls.backend.id,
                "external_id": 1001,
            }
        )

    def _rooms(self, *places):
        with trap_jobs():
            return self.env["pms.room"].create(
                [
                    {
                        "pms_property_id": self.pms_property.id,
                        "name": "Room %s" % number,
                        "short_name": "W%s" % number,
                        "room_type_id": self.room_type_a.id,
                        "capacity": capacity,
                        "children_capacity": children_capacity,
                    }
                    for number, (capacity, children_capacity) in enumerate(
                        places, start=1
                    )
                ]
            )

    def _reorganize(self, booking):
        adapter = SimpleNamespace(
            backend_record=SimpleNamespace(
                backend_type_id=SimpleNamespace(
                    child_id=SimpleNamespace(ota_ids=_NoOtaMapping())
                )
            ),
            component=lambda usage: None,
        )
        adapter._get_default_occupancy = functools.partial(
            ChannelWubookPmsFolioAdapter._get_default_occupancy, adapter
        )
        values = [booking]
        with mock.patch(GET_ROOM_TYPE, return_value=self.room_type_a):
            ChannelWubookPmsFolioAdapter._reorg_folio_data(adapter, values)
        return values[0]["reservations"]

    def _booking_without_occupancy(self):
        """Two rooms and no word on who stays in each of them."""
        booking = _booking({})
        room = booking["booked_rooms"][0]
        booking["booked_rooms"].append(dict(room))
        booking["rooms_occupancies"] = []
        return booking

    def test_children_places_go_out_with_the_occupancy(self):
        self._rooms((2, 1))
        self.assertEqual(self.binding.occupancy, 3)

    def test_occupancy_is_the_one_every_room_holds(self):
        self._rooms((2, 1), (2, 0))
        self.assertEqual(self.binding.occupancy, 2)

    def test_booking_without_occupancy_gets_the_default_one(self):
        self._rooms((3, 0), (3, 0))
        self.room_type_a.default_occupancy = 2
        reservations = self._reorganize(self._booking_without_occupancy())
        self.assertEqual([r["occupancy"] for r in reservations], [2, 2])

    def test_room_type_priced_regardless_of_occupancy(self):
        self._rooms((3, 0), (3, 0))
        self.room_type_a.default_occupancy = 0
        reservations = self._reorganize(self._booking_without_occupancy())
        self.assertEqual([r["occupancy"] for r in reservations], [3, 3])

    def test_booking_occupancy_is_kept(self):
        self._rooms((3, 0))
        self.room_type_a.default_occupancy = 2
        booking = _booking({})
        booking["men"] = 1
        reservations = self._reorganize(booking)
        self.assertEqual(reservations[0]["occupancy"], 1)
