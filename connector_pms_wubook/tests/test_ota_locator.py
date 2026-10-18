# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""Tests for the booking locator some channels send besides the reservation
code.

Bed banks, for instance, identify a booking with a locator of their own,
which Wubook passes on in the booking ``ancillary``, and they look for it
on the invoices they pay. These tests pin that the adapter hands it over to
every reservation of the booking, that the import mapper adds it to the
external reference, and that this reference reaches the folio, which is
what the invoice prints.
"""

import datetime
from types import SimpleNamespace
from unittest import mock

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.connector_pms_wubook.models.pms_folio.adapter import (
    ChannelWubookPmsFolioAdapter,
)
from odoo.addons.connector_pms_wubook.models.pms_folio.mapper_import import (
    ChannelWubookPmsFolioChildMapperImport,
)
from odoo.addons.connector_pms_wubook.models.pms_reservation.mapper_import import (
    ChannelWubookPmsReservationMapperImport,
)
from odoo.addons.pms.tests.common import TestPms

GET_ROOM_TYPE = (
    "odoo.addons.connector_pms_wubook.models.pms_folio.mapper_import.get_room_type"
)


class _NoOtaMapping:
    """An OTA mapping without the channel: the booking gets no agency."""

    def filtered(self, func):
        return self

    def __bool__(self):
        return False


def _adapter():
    # For a booking that carries the occupancy of its rooms, the
    # reorganization only reads the OTA mapping of the backend type, so it is
    # exercised without the component machinery nor a Wubook backend.
    return SimpleNamespace(
        backend_record=SimpleNamespace(
            backend_type_id=SimpleNamespace(
                child_id=SimpleNamespace(ota_ids=_NoOtaMapping())
            )
        )
    )


def _booking(ancillary):
    """A one-room channel booking shaped as Wubook sends it."""
    return {
        "id_channel": 198,
        "channel_reservation_code": "12345678",
        "channel_data": {"addons_embedded": False},
        "ancillary": ancillary,
        "customer_notes": "",
        "arrival_hour": None,
        "men": 2,
        "children": 0,
        "boards": "",
        "booked_rooms": [
            {
                "room_id": 1001,
                "ancillary": {},
                "roomdays": [
                    {
                        "ancillary": {},
                        "rate_id": 2001,
                        "price": 67.5,
                        "day": datetime.date(2026, 7, 6),
                        "board_code": "nb",
                    },
                ],
            }
        ],
    }


class TestFolioAdapterOtaLocator(TransactionCase):
    def _reorganize(self, booking):
        values = [booking]
        ChannelWubookPmsFolioAdapter._reorg_folio_data(_adapter(), values)
        return values[0]["reservations"]

    def test_locator_is_handed_over_to_the_reservation(self):
        reservations = self._reorganize(
            _booking({"Channel reservation id": "12345678", "Locator": "AB12CD"})
        )
        self.assertEqual(len(reservations), 1)
        self.assertEqual(reservations[0]["ota_reservation_code"], "12345678")
        self.assertEqual(reservations[0]["ota_locator"], "AB12CD")

    def test_every_room_of_the_booking_gets_the_locator(self):
        booking = _booking({"Locator": "AB12CD"})
        room = booking["booked_rooms"][0]
        booking["booked_rooms"].append(dict(room, room_id=1002))
        booking["rooms_occupancies"] = [
            {"id": 1001, "occupancy": 2},
            {"id": 1002, "occupancy": 1},
        ]
        reservations = self._reorganize(booking)
        self.assertEqual(
            [reservation["ota_locator"] for reservation in reservations],
            ["AB12CD", "AB12CD"],
        )

    def test_booking_without_locator(self):
        reservations = self._reorganize(_booking({"Detected Board": "nb"}))
        self.assertFalse(reservations[0]["ota_locator"])

    def test_ancillary_that_is_not_a_mapping(self):
        for ancillary in (None, [], ""):
            with self.subTest(ancillary=ancillary):
                reservations = self._reorganize(_booking(ancillary))
                self.assertFalse(reservations[0]["ota_locator"])


class TestReservationMapperExternalReference(TransactionCase):
    def _map(self, code, locator):
        # The mapping reads the incoming record only, so it is exercised
        # without the component machinery nor a Wubook backend.
        return ChannelWubookPmsReservationMapperImport.external_reference(
            None, {"ota_reservation_code": code, "ota_locator": locator}
        )

    def test_locator_is_added_to_the_reservation_code(self):
        self.assertEqual(
            self._map("12345678", "AB12CD"),
            {"external_reference": "12345678 / AB12CD"},
        )

    def test_without_locator_pms_computes_the_reference(self):
        """pms then takes the reservation code as external reference."""
        self.assertIsNone(self._map("12345678", False))

    def test_locator_equal_to_the_reservation_code(self):
        self.assertIsNone(self._map("AB12CD", "AB12CD"))

    def test_locator_without_reservation_code(self):
        self.assertEqual(self._map(False, "AB12CD"), {"external_reference": "AB12CD"})

    def test_mapped_on_updates_too(self):
        """A modification of the booking updates the folio and creates the
        new reservations from there, so the mapping cannot be create-only."""
        method = ChannelWubookPmsReservationMapperImport.external_reference
        self.assertTrue(method.is_mapping)
        self.assertFalse(getattr(method, "only_create", False))


@tagged("post_install", "-at_install")
class TestImportedExternalReference(TestPms):
    """The external reference of the folio is what its invoices print.

    ``post_install`` because the fixtures of TestPms need the columns that
    modules loaded later add to ``pms.property``.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.room_type = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [cls.pms_property1.id],
                "name": "Double Locator",
                "default_code": "DBL_LOC",
                "class_id": cls.room_type_class1.id,
            }
        )
        cls.env["pms.room"].create(
            {
                "pms_property_id": cls.pms_property1.id,
                "name": "101",
                "room_type_id": cls.room_type.id,
                "capacity": 2,
            }
        )
        cls.sale_channel = cls.env["pms.sale.channel"].create(
            {"name": "Direct Locator", "channel_type": "direct"}
        )
        cls.partner = cls.env["res.partner"].create(
            {"firstname": "Locator", "lastname": "Guest"}
        )

    def _import_reservation(self, **values):
        checkin = fields.Date.today()
        values.update(
            {
                "room_type_id": self.room_type.id,
                "checkin": checkin,
                "checkout": checkin + datetime.timedelta(days=2),
            }
        )
        # Like the channel import, the folio is created with its reservations
        # and everything is recomputed at the end. The references of the folio
        # and of its reservations depend on each other, so reading the
        # reservation first would compute the folio from a reference that is
        # still being computed.
        folio = self.env["pms.folio"].create(
            {
                "pms_property_id": self.pms_property1.id,
                "partner_id": self.partner.id,
                "sale_channel_origin_id": self.sale_channel.id,
                "reservation_ids": [(0, 0, values)],
            }
        )
        self.env.flush_all()
        return folio.reservation_ids

    def test_imported_reference_reaches_the_folio(self):
        reservation = self._import_reservation(
            ota_reservation_code="12345678",
            external_reference="12345678 / AB12CD",
        )
        self.assertEqual(reservation.external_reference, "12345678 / AB12CD")
        self.assertEqual(reservation.folio_id.external_reference, "12345678 / AB12CD")

    def test_reservation_code_without_imported_reference(self):
        reservation = self._import_reservation(ota_reservation_code="12345678")
        self.assertEqual(reservation.external_reference, "12345678")
        self.assertEqual(reservation.folio_id.external_reference, "12345678")

    def test_modification_keeps_the_reference_of_existing_reservations(self):
        """A modification updates the reservations that match the booking and
        creates the others: only the new ones take the imported reference, so
        a reference set before, even by hand, is kept."""
        existing = self._import_reservation(ota_reservation_code="12345678")
        existing.external_reference = "Edited by hand"
        nights = [existing.checkin + datetime.timedelta(days=n) for n in range(2)]
        moved_nights = [night + datetime.timedelta(days=2) for night in nights]
        imported = {
            "external_reference": "12345678 / AB12CD",
            "reservation_line_ids": [],
        }
        # The child mapper is exercised with what its reservation mapper
        # returns, so the matching runs against real reservations without a
        # Wubook backend.
        child_mapper = SimpleNamespace(
            component=lambda usage: None,
            skip_item=lambda map_record: False,
            get_item_values=lambda map_record, to_attr, options: dict(imported),
        )
        mapper = SimpleNamespace(
            map_record=lambda item, parent=None: SimpleNamespace(
                source=item, parent=parent
            )
        )
        items = [
            {"room_id": 1001, "lines": [{"day": night} for night in nights]},
            {"room_id": 1001, "lines": [{"day": night} for night in moved_nights]},
        ]
        binding = SimpleNamespace(reservation_ids=existing.folio_id.reservation_ids)
        with mock.patch(GET_ROOM_TYPE, return_value=self.room_type):
            mapped = ChannelWubookPmsFolioChildMapperImport.get_all_items(
                child_mapper,
                mapper,
                items,
                SimpleNamespace(source={"was_modified": 1}),
                "reservation_ids",
                {"binding": binding},
            )
        self.assertEqual(mapped[0]["id"], existing.id)
        self.assertNotIn("external_reference", mapped[0])
        self.assertNotIn("id", mapped[1])
        self.assertEqual(mapped[1]["external_reference"], "12345678 / AB12CD")
