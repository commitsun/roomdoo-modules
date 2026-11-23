# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""Tests for the tourist tax Booking.com puts inside the room rates.

A hotel can set the tax up in Booking.com as part of the price. The rates
Wubook sends then already have it, and the PMS adds its own tourist tax to
the reservation on top. These tests pin that the adapter tells those bookings
apart and that the import takes the tax out of the nights, so the folio adds
up to what Booking.com collected.
"""

import datetime
from functools import partial
from types import SimpleNamespace

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.connector_pms_wubook.models.pms_folio.adapter import (
    ChannelWubookPmsFolioAdapter,
)
from odoo.addons.connector_pms_wubook.models.pms_folio.importer import (
    ChannelWubookPmsFolioImporter,
)
from odoo.addons.pms.tests.common import TestPms

# Taxes of a room as Booking.com lists them, with the name in the language of
# the account
VAT = {
    "amount_included": True,
    "per_night": False,
    "description": "IVA",
    "currency": "EUR",
    "amount": 5.72,
    "per_person": False,
}
TOURIST_TAX = {
    "amount_included": True,
    "per_night": True,
    "description": "Impuesto municipal",
    "currency": "EUR",
    "amount": 3.3,
    "per_person": True,
}
PARKING = {
    "amount_included": True,
    "per_night": True,
    "description": "suplemento de parking",
    "currency": "EUR",
    "amount": 12.0,
    "per_person": False,
}


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
        ),
        _tourist_tax_included=partial(
            ChannelWubookPmsFolioAdapter._tourist_tax_included, None
        ),
    )


def _booking(extracomponents, id_channel=2):
    """A one-room booking shaped as Wubook sends it."""
    return {
        "id_channel": id_channel,
        "channel_reservation_code": "12345678",
        "channel_data": {"vat_included": 1},
        "ancillary": {"Detected Board": "nb"},
        "customer_notes": "",
        "arrival_hour": None,
        "men": 2,
        "children": 0,
        "boards": "",
        "booked_rooms": [
            {
                "room_id": 1001,
                "ancillary": {
                    "guests_adults": 2,
                    "guests_children": 0,
                    "extracomponents": extracomponents,
                },
                "roomdays": [
                    {
                        "ancillary": {},
                        "rate_id": 2001,
                        "price": 66.24,
                        "day": datetime.date(2026, 11, 2),
                        "board_code": "nb",
                    },
                ],
            }
        ],
    }


class TestFolioAdapterTouristTaxIncluded(TransactionCase):
    def _included(self, booking):
        values = [booking]
        ChannelWubookPmsFolioAdapter._reorg_folio_data(_adapter(), values)
        return values[0]["reservations"][0]["ota_tourist_tax_included"]

    def test_tax_in_the_rates(self):
        self.assertTrue(self._included(_booking([VAT, TOURIST_TAX])))

    def test_tax_paid_apart(self):
        """The guest pays it at the hotel: the rates do not have it."""
        tax = dict(TOURIST_TAX, amount_included=False)
        self.assertFalse(self._included(_booking([VAT, tax])))

    def test_vat_is_not_the_tourist_tax(self):
        self.assertFalse(self._included(_booking([VAT])))
        vat = dict(VAT, description="Mehrwertsteuer")
        self.assertFalse(self._included(_booking([vat])))

    def test_other_charges_not_per_person(self):
        self.assertFalse(self._included(_booking([VAT, PARKING])))

    def test_room_without_taxes(self):
        booking = _booking([])
        del booking["booked_rooms"][0]["ancillary"]["extracomponents"]
        self.assertFalse(self._included(booking))

    def test_only_booking(self):
        """Other channels do not send the taxes this way."""
        self.assertFalse(self._included(_booking([TOURIST_TAX], id_channel=198)))


class TestFolioImporterRemembersTouristTax(TransactionCase):
    def _import_dependencies(self, *included):
        importer = SimpleNamespace(_import_dependency=lambda *args, **kwargs: None)
        ChannelWubookPmsFolioImporter._import_dependencies(
            importer,
            {
                "reservation_code": 1,
                "modified_reservations": [],
                "reservations": [
                    {
                        "room_id": 1001,
                        "board": None,
                        "rate_id": 2001,
                        "ota_tourist_tax_included": value,
                    }
                    for value in included
                ],
            },
            None,
        )
        return importer._tourist_tax_included

    def test_any_room_with_the_tax_in_the_rates(self):
        self.assertTrue(self._import_dependencies(False, True))

    def test_no_room_with_the_tax_in_the_rates(self):
        self.assertFalse(self._import_dependencies(False, False))


@tagged("post_install", "-at_install")
class TestDeductIncludedTouristTax(TestPms):
    """``post_install`` because the fixtures of TestPms need the columns that
    modules loaded later add to ``pms.property``."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.room_type = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [cls.pms_property1.id],
                "name": "Double Tourist Tax",
                "default_code": "DBL_TAX",
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
            {"name": "Channel Tourist Tax", "channel_type": "direct"}
        )
        cls.partner = cls.env["res.partner"].create(
            {"firstname": "Tourist", "lastname": "Guest"}
        )
        # 1.65 per adult and night, up to the 5th night
        cls.tourist_tax = cls.env["product.product"].create(
            {
                "name": "Tourist Tax",
                "list_price": 1.65,
                "taxes_id": [(6, 0, [])],
                "is_tourist_tax": True,
                "per_person": True,
                "tourist_tax_apply_from_night": 1,
                "tourist_tax_apply_to_night": 5,
                "tourist_tax_min_age": 18,
            }
        )

    def _import_reservation(self, prices):
        """A Booking.com reservation as the import creates it: blocked, and
        with the price of each night the channel sent."""
        checkin = fields.Date.today() + datetime.timedelta(days=1)
        folio = self.env["pms.folio"].create(
            {
                "pms_property_id": self.pms_property1.id,
                "partner_id": self.partner.id,
                "sale_channel_origin_id": self.sale_channel.id,
                "reservation_ids": [
                    (
                        0,
                        0,
                        {
                            "pms_property_id": self.pms_property1.id,
                            "room_type_id": self.room_type.id,
                            "pricelist_id": self.pricelist1.id,
                            "adults": 2,
                            "blocked": True,
                            "reservation_line_ids": [
                                (
                                    0,
                                    0,
                                    {
                                        "date": checkin + datetime.timedelta(days=n),
                                        "price": price,
                                    },
                                )
                                for n, price in enumerate(prices)
                            ],
                        },
                    )
                ],
            }
        )
        self.env.flush_all()
        return folio.reservation_ids

    def _deduct(self, reservation):
        # The deduction only needs the environment of the import, so it is
        # exercised without the component machinery nor a Wubook backend.
        ChannelWubookPmsFolioImporter._deduct_included_tourist_tax(
            SimpleNamespace(env=self.env), reservation.folio_id, reservation
        )
        self.env.flush_all()

    def _tax(self, reservation):
        return sum(
            reservation._get_existing_tourist_tax_services().mapped("price_total")
        )

    def test_nights_give_up_the_tax(self):
        reservation = self._import_reservation([66.24])
        self.assertAlmostEqual(self._tax(reservation), 3.30, places=2)
        self.assertAlmostEqual(reservation.folio_id.amount_total, 69.54, places=2)

        self._deduct(reservation)

        self.assertAlmostEqual(reservation.reservation_line_ids.price, 62.94, places=2)
        self.assertAlmostEqual(self._tax(reservation), 3.30, places=2)
        self.assertAlmostEqual(reservation.folio_id.amount_total, 66.24, places=2)
        self.assertIn("tourist tax", reservation.folio_id.message_ids[0].body)

    def test_nights_beyond_the_tax_keep_their_price(self):
        reservation = self._import_reservation([50.0] * 7)

        self._deduct(reservation)

        self.assertEqual(
            [
                round(price, 2)
                for price in reservation.reservation_line_ids.sorted("date").mapped(
                    "price"
                )
            ],
            [46.7] * 5 + [50.0] * 2,
        )
        self.assertAlmostEqual(reservation.folio_id.amount_total, 350.0, places=2)

    def test_cancelled_reservations_are_left_alone(self):
        reservation = self._import_reservation([66.24])
        reservation.with_context(force_write_blocked=True).action_cancel()
        prices = reservation.reservation_line_ids.mapped("price")
        messages = reservation.folio_id.message_ids

        self._deduct(reservation)

        self.assertEqual(reservation.reservation_line_ids.mapped("price"), prices)
        self.assertEqual(reservation.folio_id.message_ids, messages)

    def test_without_tourist_tax_nothing_changes(self):
        self.tourist_tax.active = False
        reservation = self._import_reservation([66.24])
        messages = reservation.folio_id.message_ids

        self._deduct(reservation)

        self.assertAlmostEqual(reservation.reservation_line_ids.price, 66.24, places=2)
        self.assertAlmostEqual(reservation.folio_id.amount_total, 66.24, places=2)
        self.assertEqual(reservation.folio_id.message_ids, messages)
