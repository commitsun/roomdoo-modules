# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""Extras sold by the channels (WuBook ``addons_list``) as folio services.

The fixtures are synthetic but keep the shape of real channel bookings: the
``price`` of an extra is the TOTAL of the line, ``req`` its total quantity
(already multiplied by nights or guests) and the booking ``amount`` does not
include the extras. No test reaches Wubook: the bookings are handed to the
importer the way the adapter leaves them, and XML-RPC is trapped.
"""

import copy
from datetime import date, datetime, time, timedelta
from unittest import mock

import psycopg2
from freezegun import freeze_time

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, tagged
from odoo.tools import html2plaintext, mute_logger

from odoo.addons.component.tests.common import TransactionComponentCase
from odoo.addons.connector_pms_wubook.models.pms_folio.channel_extras import (
    normalize_extra_name,
    parse_wubook_extras,
    plan_service_lines,
    read_channel_extras,
)
from odoo.addons.queue_job.exception import RetryableJobError

ROOM_ID = 111
RATE_ID = 1001
NIGHT_PRICE = 80.0
SYNC_MODULE = "odoo.addons.connector_pms_wubook.models.pms_folio.binding_channel_extras"
SYNC_PATH = f"{SYNC_MODULE}.ChannelWubookPmsFolioBinding._sync_channel_extras"
MAPPER_MODULE = "odoo.addons.connector_pms_wubook.models.pms_folio.mapper_import"
ERRORS_TO_RETRY = (
    psycopg2.OperationalError("could not serialize access"),
    RetryableJobError("try again later"),
)


def _addon(name, req, price):
    """An extra as a channel sends it: ``price`` is the total of the line."""
    return {"name": name, "req": req, "price": price, "descr": ""}


class TestChannelExtrasParsing(TransactionCase):
    """Pure helpers: no database involved."""

    def test_normalize_extra_name(self):
        self.assertEqual(
            normalize_extra_name("I'm going with my dog."), "i m going with my dog"
        )
        self.assertEqual(
            normalize_extra_name('Club Termal "El Lago"'),
            "club termal el lago",
        )
        self.assertEqual(
            normalize_extra_name("Descenso del Río en Canoa"),
            "descenso del rio en canoa",
        )
        self.assertEqual(
            normalize_extra_name("Barco,niños 2 a 10 años , menor gratis"),
            "barco ninos 2 a 10 anos menor gratis",
        )
        self.assertEqual(normalize_extra_name("  PARKING "), "parking")
        self.assertEqual(normalize_extra_name("Parking"), "parking")
        self.assertEqual(normalize_extra_name("Parking 2"), "parking 2")
        self.assertEqual(normalize_extra_name("Café_au-lait"), "cafe au lait")
        for empty in ("", None, " ", "¿?!", "--"):
            with self.subTest(name=empty):
                self.assertEqual(normalize_extra_name(empty), "")

    def test_parse_channel_shape(self):
        booking = {
            "addons_list": [
                _addon("Parking", 3, 24.0),
                _addon("cradle", 4, 40.0),
            ]
        }
        self.assertEqual(
            parse_wubook_extras(booking),
            [
                {"key": "parking", "name": "Parking", "qty": 3, "amount": 24.0},
                {"key": "cradle", "name": "cradle", "qty": 4, "amount": 40.0},
            ],
        )

    def test_parse_booking_engine_shape(self):
        booking = {
            "addons_list": [
                {
                    "id": 7,
                    "name": "Breakfast",
                    "number": 4,
                    "perday": 1,
                    "id_room": ROOM_ID,
                    "price": 48.0,
                }
            ]
        }
        self.assertEqual(
            parse_wubook_extras(booking),
            [{"key": "breakfast", "name": "Breakfast", "qty": 4, "amount": 48.0}],
        )

    def test_parse_aggregates_the_same_extra(self):
        booking = {
            "addons_list": [_addon("Parking", 2, 16.0), _addon(" parking. ", 1, 8.5)]
        }
        self.assertEqual(
            parse_wubook_extras(booking),
            [{"key": "parking", "name": "Parking", "qty": 3, "amount": 24.5}],
        )

    def test_parse_without_extras(self):
        for booking in (None, {}, {"addons_list": None}, {"addons_list": []}):
            with self.subTest(booking=booking):
                self.assertEqual(parse_wubook_extras(booking), [])
        self.assertEqual(parse_wubook_extras({"addons_list": "Parking"}), [])

    def test_parse_skips_and_defaults(self):
        booking = {
            "addons_list": [
                "Parking",
                _addon("", 1, 10.0),
                _addon("?", 1, 10.0),
                _addon("No price", 1, None),
                _addon("Bad price", 1, "ten"),
                _addon("No quantity", 0, 5.0),
                _addon("Bad quantity", "x", 6.0),
                {"name": "Missing quantity", "price": 7.0},
                _addon("Free", 2, 0.0),
            ]
        }
        self.assertEqual(
            [(x["name"], x["qty"], x["amount"]) for x in parse_wubook_extras(booking)],
            [
                ("No quantity", 1, 5.0),
                ("Bad quantity", 1, 6.0),
                ("Missing quantity", 1, 7.0),
                ("Free", 2, 0.0),
            ],
        )

    def test_read_channel_extras(self):
        booking = {
            "id_channel": 197,
            "currency": "EUR",
            "addons_list": [_addon("Parking", 1, 8.0)],
            "channel_data": {
                "addons_embedded": True,
                "split_reservation": {"n_stream": 2, "tot_stream": 2},
            },
        }
        self.assertEqual(
            read_channel_extras(booking),
            {
                "channel": 197,
                "currency": "EUR",
                "split": [2, 2],
                "items": [
                    {"key": "parking", "name": "Parking", "qty": 1, "amount": 8.0}
                ],
            },
        )
        del booking["channel_data"]["split_reservation"]
        self.assertIsNone(read_channel_extras(booking)["split"])

    def _plan(
        self, qty, amount, nights, per_day=True, consumed_on="before", onboard=None
    ):
        checkin = date(2026, 7, 1)
        lines = plan_service_lines(
            qty,
            amount,
            checkin,
            checkin + timedelta(days=nights),
            per_day,
            consumed_on,
            onboard_date=None if onboard is None else checkin + timedelta(onboard),
        )
        # Whatever the spread, the total is exactly the channel amount
        self.assertAlmostEqual(sum(q * p for _d, q, p in lines), amount, places=2)
        return [((d - checkin).days, q, p) for d, q, p in lines]

    def test_plan_per_night_extra(self):
        # Parking, 3 nights, 8 EUR per night
        self.assertEqual(
            self._plan(3, 24.0, 3), [(0, 1, 8.0), (1, 1, 8.0), (2, 1, 8.0)]
        )

    def test_plan_consumed_after_the_night(self):
        # Breakfast for 2 guests, 4 nights: served from the day after checkin
        self.assertEqual(
            self._plan(8, 96.0, 4, consumed_on="after"),
            [(1, 2, 12.0), (2, 2, 12.0), (3, 2, 12.0), (4, 2, 12.0)],
        )

    def test_plan_one_shot_extra(self):
        # An experience for 2 guests goes on the checkout date, like pms does
        self.assertEqual(self._plan(2, 40.0, 3, per_day=False), [(3, 2, 20.0)])
        self.assertEqual(
            self._plan(2, 40.0, 3, per_day=False, consumed_on="checkin"),
            [(0, 2, 20.0)],
        )
        # pms only spreads per day products consumed before / after the night
        self.assertEqual(self._plan(2, 40.0, 3, consumed_on="checkout"), [(3, 2, 20.0)])

    def test_plan_extra_on_board(self):
        # Once the guests are on board, pms charges what is not per day on the
        # current date of the hotel
        self.assertEqual(
            self._plan(2, 40.0, 3, per_day=False, onboard=1), [(1, 2, 20.0)]
        )
        # Not what is consumed on checkin nor the per day products
        self.assertEqual(
            self._plan(2, 40.0, 3, per_day=False, consumed_on="checkin", onboard=1),
            [(0, 2, 20.0)],
        )
        self.assertEqual(
            self._plan(2, 40.0, 3, consumed_on="checkout", onboard=1), [(3, 2, 20.0)]
        )
        self.assertEqual(
            self._plan(3, 24.0, 3, onboard=1), [(0, 1, 8.0), (1, 1, 8.0), (2, 1, 8.0)]
        )

    def test_plan_fewer_units_than_nights(self):
        self.assertEqual(
            self._plan(3, 24.0, 4), [(0, 1, 8.0), (1, 1, 8.0), (2, 1, 8.0)]
        )

    def test_plan_units_not_multiple_of_nights(self):
        self.assertEqual(self._plan(5, 50.0, 2), [(0, 5, 10.0)])

    def test_plan_several_rooms(self):
        # Channels count the parking of 2 rooms as nights x rooms
        self.assertEqual(self._plan(10, 40.0, 5), [(n, 2, 4.0) for n in range(5)])

    def test_plan_inexact_unit_price(self):
        # 10.00 / 3 is not a whole number of cents: one line keeps the total
        self.assertEqual(self._plan(3, 10.0, 3), [(0, 1, 10.0)])
        self.assertEqual(
            self._plan(3, 10.0, 3, per_day=False, consumed_on="checkin"),
            [(0, 1, 10.0)],
        )

    def test_plan_free_extra(self):
        self.assertEqual(self._plan(2, 0.0, 2), [(0, 1, 0.0), (1, 1, 0.0)])

    def test_plan_stay_without_nights(self):
        self.assertEqual(self._plan(1, 9.0, 0), [(0, 1, 9.0)])


def _setup_channel_extras(cls):
    """A hotel connected to Wubook whose channel 197 imports its extras."""
    spain = cls.env.ref("base.es")
    cls.company = cls.env["res.company"].create(
        {"name": "Channel Extras Co", "country_id": spain.id}
    )
    cls.tax_included = cls.env["account.tax"].create(
        {
            "name": "Extras VAT 10% included",
            "amount": 10.0,
            "amount_type": "percent",
            "type_tax_use": "sale",
            "price_include": True,
            "company_id": cls.company.id,
            "country_id": spain.id,
        }
    )
    cls.tax_excluded = cls.env["account.tax"].create(
        {
            "name": "Extras VAT 21% excluded",
            "amount": 21.0,
            "amount_type": "percent",
            "type_tax_use": "sale",
            "price_include": False,
            "company_id": cls.company.id,
            "country_id": spain.id,
        }
    )
    cls.availability_plan = cls.env["pms.availability.plan"].create(
        {"name": "Channel Extras Plan"}
    )
    cls.pricelist = cls.env["product.pricelist"].create(
        {
            "name": "Channel Extras Pricelist",
            "is_pms_available": True,
            "availability_plan_id": cls.availability_plan.id,
        }
    )
    cls.pms_property = cls.env["pms.property"].create(
        {
            "name": "Channel Extras Hotel",
            "company_id": cls.company.id,
            "default_pricelist_id": cls.pricelist.id,
        }
    )
    room_type_class = cls.env["pms.room.type.class"].create(
        {"name": "Channel Extras Class", "default_code": "CXC"}
    )
    room_product = cls.env["product.product"].create(
        {"name": "Channel Extras Double", "type": "service", "list_price": 80.0}
    )
    cls.room_type = cls.env["pms.room.type"].create(
        {
            "name": "Channel Extras Double",
            "default_code": "CXD",
            "class_id": room_type_class.id,
            "product_id": room_product.id,
            "pms_property_ids": [(6, 0, cls.pms_property.ids)],
        }
    )
    for number in range(1, 4):
        cls.env["pms.room"].create(
            {
                "name": f"Extras {number}",
                "room_type_id": cls.room_type.id,
                "pms_property_id": cls.pms_property.id,
                "capacity": 2,
            }
        )
    cls.agency = cls._make_agency("Booking engine with extras")
    cls.other_agency = cls._make_agency("Channel without extras")
    backend_type = cls.env["channel.wubook.backend.type"].create(
        {
            "name": "Channel Extras Type",
            "model_type_id": cls.env.ref(
                "connector_pms_wubook.model_channel_wubook_backend_type"
            ).id,
            "direct_channel_type_id": cls.env.ref(
                "connector_pms_wubook.main_pms_sale_channel_wubook"
            ).id,
            "ota_ids": [
                (
                    0,
                    0,
                    {
                        "wubook_ota": 197,
                        "agency_id": cls.agency.id,
                        "import_extras": True,
                    },
                ),
                (0, 0, {"wubook_ota": 2, "agency_id": cls.other_agency.id}),
            ],
        }
    )
    cls.backend = cls.env["channel.wubook.backend"].create(
        {
            "name": "Channel Extras Backend",
            "pms_property_id": cls.pms_property.id,
            "backend_type_id": backend_type.parent_id.id,
            "username": "X",
            "password": "X",
            "property_code": "X",
            "pkey": "X",
            "pricelist_external_id": RATE_ID,
            "wubook_payment_method_line_id": cls.env["account.payment.method.line"]
            .search([], limit=1)
            .id,
            "extras_import_start": fields.Datetime.now() - timedelta(days=1),
        }
    )
    cls.env["channel.wubook.pms.room.type"].create(
        {
            "odoo_id": cls.room_type.id,
            "backend_id": cls.backend.id,
            "external_id": ROOM_ID,
        }
    )
    cls.env["channel.wubook.product.pricelist"].create(
        {
            "odoo_id": cls.pricelist.id,
            "backend_id": cls.backend.id,
            "external_id": RATE_ID,
        }
    )
    product = cls.env["product.product"]
    cls.parking = product.create(cls._service_vals("Parking", 9.0, per_day=True))
    cls.breakfast = product.create(
        cls._service_vals(
            "Breakfast", 12.0, per_day=True, per_person=True, consumed_on="after"
        )
    )
    cls.cot = product.create(cls._service_vals("Cot", 10.0, per_day=True))
    cls.boat_trip = product.create(cls._service_vals("Boat trip", 20.0))
    cls.fallback = product.create(cls._service_vals("Channel extra", 0.0))
    cls.untaxed = product.create(
        cls._service_vals("Untaxed excursion", 30.0, tax=cls.tax_excluded)
    )
    extras = cls.env["channel.wubook.backend.extra"]
    for name, mapped in (
        ("Parking", cls.parking),
        ("Breakfast", cls.breakfast),
        ("cradle", cls.cot),
        ("Boat ride", cls.boat_trip),
    ):
        extras.create(
            {"backend_id": cls.backend.id, "name": name, "product_id": mapped.id}
        )
    cls.checkin = date.today() + timedelta(days=20)


@tagged("post_install", "-at_install")
class TestFolioChannelExtras(TransactionComponentCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _setup_channel_extras(cls)

    @classmethod
    def _make_agency(cls, name):
        channel = cls.env["pms.sale.channel"].create(
            {"name": name, "channel_type": "indirect"}
        )
        return cls.env["res.partner"].create(
            {
                "name": name,
                "is_agency": True,
                "sale_channel_id": channel.id,
                "property_product_pricelist": cls.pricelist.id,
            }
        )

    @classmethod
    def _service_vals(cls, name, price, tax=None, **flags):
        return {
            "name": name,
            "type": "service",
            "list_price": price,
            "taxes_id": [(6, 0, (tax or cls.tax_included).ids)],
            **flags,
        }

    def setUp(self):
        super().setUp()
        # Nothing here may reach Wubook
        network = mock.patch(
            "xmlrpc.client.Server",
            side_effect=AssertionError("Wubook must not be called"),
        )
        network.start()
        self.addCleanup(network.stop)

    # helpers

    def _booking(self, code, addons=(), nights=3, checkin=None, rooms=1, **values):
        """A booking as Wubook sends it (before the adapter formats it)."""
        checkin = checkin or self.checkin
        fmt = "%d/%m/%Y"
        roomdays = [
            {
                "day": (checkin + timedelta(days=night)).strftime(fmt),
                "price": NIGHT_PRICE,
                "rate_id": RATE_ID,
                "board_code": "",
                "ancillary": {},
            }
            for night in range(nights)
        ]
        booking = {
            "reservation_code": code,
            "channel_reservation_code": f"EXTRAS-{code}",
            "id_channel": 197,
            "status": 1,
            "was_modified": 0,
            "modified_reservations": [],
            "amount": NIGHT_PRICE * nights * rooms,
            "currency": self.pricelist.currency_id.name,
            "addons_list": [dict(addon) for addon in addons],
            "channel_data": {"addons_embedded": True},
            "booked_rooms": [
                {"room_id": ROOM_ID, "roomdays": copy.deepcopy(roomdays)}
                for _room in range(rooms)
            ],
            "rooms_occupancies": [
                {"id": ROOM_ID, "occupancy": 2} for _room in range(rooms)
            ],
            "boards": "",
            "men": 2,
            "children": 0,
            "customer_notes": "",
            "arrival_hour": "--",
            "customer_name": "Alex",
            "customer_surname": "Sample",
            "customer_mail": "guest@example.com",
            "customer_phone": "",
            "customer_language_iso": "en",
            "payment_gateway_fee": "",
            "ancillary": {},
            "date_arrival": checkin.strftime(fmt),
            "date_departure": (checkin + timedelta(days=nights)).strftime(fmt),
        }
        booking.update(values)
        return booking

    def _import(self, booking):
        """Import ``booking`` the way a push from Wubook does."""
        with self.backend.work_on("channel.wubook.pms.folio") as work:
            adapter = work.component(usage="backend.adapter")
            values = [copy.deepcopy(booking)]
            adapter._format_folio_data(values)
            adapter._reorg_folio_data(values)
            importer = work.component_by_name("channel.wubook.pms.folio.importer")
            importer.run(booking["reservation_code"], external_data=values[0])
        return self._binding(booking)

    def _binding(self, booking):
        return self.env["channel.wubook.pms.folio"].search(
            [
                ("backend_id", "=", self.backend.id),
                ("external_id", "=", booking["reservation_code"]),
            ]
        )

    def _services(self, folio):
        return folio.service_ids.filtered("channel_extra_key")

    def _service(self, folio, key):
        return self._services(folio).filtered(
            lambda service: service.channel_extra_key == key
        )

    def _lines(self, service):
        checkin = service.reservation_id.checkin
        return [
            ((line.date - checkin).days, line.day_qty, line.price_unit)
            for line in service.service_line_ids.sorted("date")
        ]

    def _notes(self, folio):
        """Notes about channel extras (pms posts its own ones as well)."""
        note = self.env.ref("mail.mt_note")
        bodies = [
            html2plaintext(message.body)
            for message in folio.message_ids
            if message.subtype_id == note and message.body
        ]
        return [body for body in bodies if "extra" in body.lower()]

    def _extras_notes(self, folio, text):
        return [body for body in self._notes(folio) if text in body]

    def _state(self, binding, key):
        return binding.channel_extras_state["items"][key]["state"]

    def _row(self, name):
        return (
            self.env["channel.wubook.backend.extra"]
            .with_context(active_test=False)
            .search(
                [
                    ("backend_id", "=", self.backend.id),
                    ("normalized_name", "=", normalize_extra_name(name)),
                ]
            )
        )

    def _put_on_board(self, folio):
        """The guests of ``folio`` checked in (how does not matter here)."""
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE pms_reservation SET state = 'onboard' WHERE folio_id = %s",
            (folio.id,),
        )
        self.env.invalidate_all()

    def _other_backend(self):
        """The backend of another hotel of the company."""
        other_property = self.env["pms.property"].create(
            {
                "name": "Channel Extras Other Hotel",
                "company_id": self.company.id,
                "default_pricelist_id": self.pricelist.id,
            }
        )
        return self.env["channel.wubook.backend"].create(
            {
                "name": "Channel Extras Other Backend",
                "pms_property_id": other_property.id,
                "backend_type_id": self.backend.backend_type_id.id,
                "username": "X",
                "password": "X",
                "property_code": "Y",
                "pkey": "X",
                "pricelist_external_id": RATE_ID,
                "wubook_payment_method_line_id": (
                    self.backend.wubook_payment_method_line_id.id
                ),
            }
        )

    def _hotel_manager(self):
        """A manager of the hotel of the backend, without connector rights."""
        return self.env["res.users"].create(
            {
                "name": "Hotel manager",
                "login": "channel.extras.manager@example.com",
                "company_id": self.company.id,
                "company_ids": [(6, 0, self.company.ids)],
                "pms_property_id": self.pms_property.id,
                "pms_property_ids": [(6, 0, self.pms_property.ids)],
                "groups_id": [
                    (
                        6,
                        0,
                        [
                            self.env.ref("base.group_user").id,
                            self.env.ref("pms.group_pms_manager").id,
                        ],
                    ),
                ],
            }
        )

    # import

    def test_import_creates_the_services(self):
        booking = self._booking(
            9101, [_addon("Parking", 3, 24.0), _addon("cradle", 3, 30.0)]
        )
        binding = self._import(booking)
        folio = binding.odoo_id
        reservation = folio.reservation_ids
        self.assertEqual(len(reservation), 1)
        parking = self._service(folio, "parking")
        cot = self._service(folio, "cradle")
        self.assertEqual(parking.reservation_id, reservation)
        self.assertEqual(parking.product_id, self.parking)
        self.assertEqual(parking.channel_extra_name, "Parking")
        self.assertEqual(parking.wubook_extra_id, self._row("Parking"))
        self.assertTrue(parking.no_auto_add_lines)
        self.assertFalse(parking.is_board_service)
        self.assertEqual(self._lines(parking), [(0, 1, 8.0), (1, 1, 8.0), (2, 1, 8.0)])
        self.assertEqual(self._lines(cot), [(0, 1, 10.0), (1, 1, 10.0), (2, 1, 10.0)])
        self.assertEqual(parking.name, self.parking.display_name)
        # The folio adds up to what the guest pays: rooms plus extras
        self.assertAlmostEqual(folio.amount_total, booking["amount"] + 54.0)
        self.assertEqual(
            binding.channel_extras,
            {
                "channel": 197,
                "currency": folio.currency_id.name,
                "split": None,
                "items": [
                    {"key": "parking", "name": "Parking", "qty": 3, "amount": 24.0},
                    {"key": "cradle", "name": "cradle", "qty": 3, "amount": 30.0},
                ],
            },
        )
        entry = binding.channel_extras_state["items"]["parking"]
        self.assertEqual(entry["state"], "applied")
        self.assertEqual(entry["product_id"], self.parking.id)
        self.assertEqual(entry["service_qty"], 3)
        self.assertEqual(entry["service_amount"], 24.0)
        self.assertFalse(self._notes(folio))

    def test_reimport_changes_nothing(self):
        booking = self._booking(9102, [_addon("Parking", 3, 24.0)])
        binding = self._import(booking)
        folio = binding.odoo_id
        lines = self._service(folio, "parking").service_line_ids
        state = binding.channel_extras_state
        self._import(booking)
        self.assertEqual(len(self._services(folio)), 1)
        self.assertEqual(self._service(folio, "parking").service_line_ids, lines)
        self.assertEqual(binding.channel_extras_state, state)
        self.assertFalse(self._notes(folio))

    def test_breakfast_is_served_after_each_night(self):
        binding = self._import(self._booking(9109, [_addon("Breakfast", 6, 72.0)]))
        service = self._service(binding.odoo_id, "breakfast")
        self.assertEqual(
            self._lines(service), [(1, 2, 12.0), (2, 2, 12.0), (3, 2, 12.0)]
        )
        self.assertEqual(service.product_qty, 6)

    def test_channel_change_updates_an_untouched_service(self):
        binding = self._import(self._booking(9103, [_addon("Parking", 3, 24.0)]))
        folio = binding.odoo_id
        service = self._service(folio, "parking")
        self._import(self._booking(9103, [_addon("Parking", 3, 27.0)]))
        self.assertEqual(self._service(folio, "parking"), service)
        self.assertEqual(self._lines(service), [(0, 1, 9.0), (1, 1, 9.0), (2, 1, 9.0)])
        self.assertEqual(
            binding.channel_extras_state["items"]["parking"]["amount"], 27.0
        )
        self.assertFalse(self._notes(folio))

    def test_staff_changes_are_kept(self):
        binding = self._import(self._booking(9104, [_addon("Parking", 3, 24.0)]))
        folio = binding.odoo_id
        service = self._service(folio, "parking")
        # Staff charge the parking at 10 a night
        service.service_line_ids.write({"price_unit": 10.0})
        self._import(self._booking(9104, [_addon("Parking", 3, 27.0)]))
        self.assertEqual(
            self._lines(service), [(0, 1, 10.0), (1, 1, 10.0), (2, 1, 10.0)]
        )
        notes = self._extras_notes(folio, "changed by hand or invoiced")
        self.assertEqual(len(notes), 1)
        self.assertIn("Parking", notes[0])
        # Imported again: neither the service nor the notes change
        self._import(self._booking(9104, [_addon("Parking", 3, 27.0)]))
        self.assertEqual(
            self._lines(service), [(0, 1, 10.0), (1, 1, 10.0), (2, 1, 10.0)]
        )
        self.assertEqual(len(self._extras_notes(folio, "changed by hand")), 1)

    def test_invoiced_service_is_not_updated(self):
        binding = self._import(self._booking(9105, [_addon("Parking", 3, 24.0)]))
        folio = binding.odoo_id
        service = self._service(folio, "parking")
        model = type(binding)
        with mock.patch.object(model, "_channel_extras_invoiced", return_value=True):
            self._import(self._booking(9105, [_addon("Parking", 3, 27.0)]))
        self.assertEqual(self._lines(service), [(0, 1, 8.0), (1, 1, 8.0), (2, 1, 8.0)])
        self.assertEqual(len(self._extras_notes(folio, "or invoiced")), 1)

    def test_real_invoices_keep_the_service(self):
        """A draft or posted invoice of the folio keeps the service as it is,
        a cancelled one no longer does."""
        chart = self.env.ref(
            "l10n_generic_coa.configurable_chart_template", raise_if_not_found=False
        )
        if not chart:
            self.skipTest("No chart of accounts to invoice with")
        self.env.user.company_ids |= self.company
        chart.try_loading(company=self.company, install_demo=False)
        # Channel bookings have no customer yet: they get simplified invoices
        self.pms_property.journal_simplified_invoice_id = self.env[
            "account.journal"
        ].search([("company_id", "=", self.company.id), ("type", "=", "sale")], limit=1)
        binding = self._import(self._booking(9150, [_addon("Parking", 3, 24.0)]))
        folio = binding.odoo_id
        service = self._service(folio, "parking")
        invoice = folio._create_invoices()
        self.assertEqual(invoice.state, "draft")
        self._import(self._booking(9150, [_addon("Parking", 3, 27.0)]))
        self.assertEqual(self._lines(service), [(0, 1, 8.0), (1, 1, 8.0), (2, 1, 8.0)])
        invoice.action_post()
        self._import(self._booking(9150, [_addon("Parking", 3, 30.0)]))
        self.assertEqual(self._lines(service), [(0, 1, 8.0), (1, 1, 8.0), (2, 1, 8.0)])
        self.assertEqual(len(self._extras_notes(folio, "or invoiced")), 2)
        invoice.button_draft()
        invoice.button_cancel()
        self._import(self._booking(9150, [_addon("Parking", 3, 33.0)]))
        self.assertEqual(
            self._lines(service), [(0, 1, 11.0), (1, 1, 11.0), (2, 1, 11.0)]
        )

    def test_service_deleted_by_staff_is_not_created_again(self):
        binding = self._import(self._booking(9106, [_addon("Parking", 3, 24.0)]))
        folio = binding.odoo_id
        self._service(folio, "parking").unlink()
        self._import(self._booking(9106, [_addon("Parking", 3, 24.0)]))
        self.assertFalse(self._services(folio))
        self.assertEqual(self._state(binding, "parking"), "deleted")
        self.assertFalse(self._notes(folio))
        # The channel changes it: still not created, staff are told once
        self._import(self._booking(9106, [_addon("Parking", 4, 32.0)]))
        self._import(self._booking(9106, [_addon("Parking", 4, 32.0)]))
        self.assertFalse(self._services(folio))
        self.assertEqual(len(self._extras_notes(folio, "managed by hand")), 1)

    def test_extra_removed_by_the_channel(self):
        binding = self._import(
            self._booking(9107, [_addon("Parking", 3, 24.0), _addon("cradle", 3, 30.0)])
        )
        folio = binding.odoo_id
        self._import(self._booking(9107, [_addon("Parking", 3, 24.0)]))
        self.assertEqual(self._services(folio).mapped("channel_extra_key"), ["parking"])
        self.assertNotIn("cradle", binding.channel_extras_state["items"])
        self.assertAlmostEqual(folio.amount_total, 3 * NIGHT_PRICE + 24.0)
        self.assertFalse(self._notes(folio))

    def test_extra_removed_by_the_channel_but_changed_by_hand(self):
        binding = self._import(
            self._booking(9108, [_addon("Parking", 3, 24.0), _addon("cradle", 3, 30.0)])
        )
        folio = binding.odoo_id
        cot = self._service(folio, "cradle")
        cot.service_line_ids[0].day_qty = 2
        self._import(self._booking(9108, [_addon("Parking", 3, 24.0)]))
        self.assertTrue(cot.exists())
        self.assertEqual(len(self._extras_notes(folio, "removed the extra cradle")), 1)

    def test_modified_stay_moves_the_services(self):
        """The channel moves the stay: the old room is cancelled as modified
        and the extras follow the booking to the new one."""
        first = self._booking(9110, [_addon("Parking", 3, 24.0)])
        binding = self._import(first)
        folio = binding.odoo_id
        old_reservation = folio.reservation_ids
        old_service = self._service(folio, "parking")
        moved = self._booking(
            9111,
            [_addon("Parking", 3, 24.0)],
            checkin=self.checkin + timedelta(days=5),
            modified_reservations=[9110],
        )
        self.assertEqual(self._import(moved), binding)
        self.assertEqual(old_reservation.state, "cancel")
        self.assertEqual(old_reservation.cancelled_reason, "modified")
        self.assertFalse(old_service.exists())
        new_reservation = folio.reservation_ids - old_reservation
        service = self._service(folio, "parking")
        self.assertEqual(service.reservation_id, new_reservation)
        self.assertEqual(service.service_line_ids[0].date, new_reservation.checkin)
        self.assertEqual(self._lines(service), [(0, 1, 8.0), (1, 1, 8.0), (2, 1, 8.0)])
        self.assertAlmostEqual(folio.amount_total, 3 * NIGHT_PRICE + 24.0)

    def _move_stay(self, code, new_code, addons):
        self._import(self._booking(code, addons))
        return self._booking(
            new_code,
            addons,
            checkin=self.checkin + timedelta(days=5),
            modified_reservations=[code],
        )

    def test_modified_stay_keeps_an_invoiced_service(self):
        moved = self._move_stay(9140, 9141, [_addon("Parking", 3, 24.0)])
        folio = self._binding({"reservation_code": 9140}).odoo_id
        old_service = self._service(folio, "parking")
        binding_model = type(self.env["channel.wubook.pms.folio"])
        with mock.patch.object(
            binding_model, "_channel_extras_invoiced", return_value=True
        ):
            binding = self._import(moved)
        self.assertTrue(old_service.exists())
        self.assertEqual(old_service.reservation_id.cancelled_reason, "modified")
        # Not charged twice: the new stay does not get it
        self.assertEqual(self._services(folio), old_service)
        self.assertEqual(self._state(binding, "parking"), "deleted")
        self.assertEqual(len(self._extras_notes(folio, "stays on the cancelled")), 1)

    def test_modified_stay_recreates_a_service_changed_by_hand(self):
        moved = self._move_stay(9142, 9143, [_addon("Parking", 3, 24.0)])
        folio = self._binding({"reservation_code": 9142}).odoo_id
        self._service(folio, "parking").service_line_ids.write({"price_unit": 10.0})
        self._import(moved)
        service = self._service(folio, "parking")
        self.assertEqual(service.reservation_id.checkin, self.checkin + timedelta(5))
        self.assertEqual(self._lines(service), [(0, 1, 8.0), (1, 1, 8.0), (2, 1, 8.0)])
        notes = self._extras_notes(folio, "without the changes made by hand")
        self.assertEqual(len(notes), 1)
        self.assertIn("30.00", notes[0])

    def test_channel_change_that_cannot_be_applied(self):
        self.parking.with_context(property=self.pms_property.id).daily_limit = 1
        binding = self._import(self._booking(9144, [_addon("Parking", 3, 24.0)]))
        folio = binding.odoo_id
        service = self._service(folio, "parking")
        # Two cars a night do not fit in the daily limit
        self._import(self._booking(9144, [_addon("Parking", 6, 48.0)]))
        self.assertEqual(self._lines(service), [(0, 1, 8.0), (1, 1, 8.0), (2, 1, 8.0)])
        self.assertEqual(binding.channel_extras_state["items"]["parking"]["qty"], 3)
        notes = self._extras_notes(folio, "could not be updated")
        self.assertEqual(len(notes), 1)
        self.assertIn("limit exceeded", notes[0])
        # Retried on every import, reported once
        self._import(self._booking(9144, [_addon("Parking", 6, 48.0)]))
        self.assertEqual(len(self._extras_notes(folio, "could not be updated")), 1)

    def test_hotel_managers_map_the_extras_of_their_hotels(self):
        extras = self.env["channel.wubook.backend.extra"]
        foreign_row = extras.create(
            {"backend_id": self._other_backend().id, "name": "Late checkout"}
        )
        binding = self._import(self._booking(9145, [_addon("Late checkout", 1, 15.0)]))
        manager = self._hotel_manager()
        # They find them in the configuration of the PMS
        menu = self.env.ref(
            "connector_pms_wubook.channel_wubook_backend_extra_pms_menu"
        )
        self.assertIn(
            menu.id, self.env["ir.ui.menu"].with_user(manager)._visible_menu_ids()
        )
        rows = extras.with_user(manager).search([])
        self.assertIn(self._row("Late checkout"), rows)
        self.assertNotIn(foreign_row, rows)
        row = self._row("Late checkout").with_user(manager)
        row.product_id = self.boat_trip
        row.action_apply_to_pending_folios()
        self.assertTrue(self._service(binding.odoo_id, "late checkout"))

    def test_hotel_managers_keep_the_rows_in_their_hotels(self):
        other_backend = self._other_backend()
        manager = self._hotel_manager()
        row = self._row("Parking")
        with self.assertRaises(AccessError) as caught:
            row.with_user(manager).write({"backend_id": other_backend.id})
        self.assertIn("Only connector managers", str(caught.exception))
        self.env["res.lang"]._activate_lang("es_ES")
        with self.assertRaises(AccessError) as caught:
            row.with_user(manager).with_context(lang="es_ES").write(
                {"backend_id": other_backend.id}
            )
        self.assertIn("Solo los administradores del conector", str(caught.exception))
        self.assertEqual(row.backend_id, self.backend)
        # Writing the backend the row already has is harmless
        row.with_user(manager).write(
            {"backend_id": self.backend.id, "action": "ignore"}
        )
        self.assertEqual(row.action, "ignore")
        # Connector managers can move it
        manager.groups_id |= self.env.ref("connector.group_connector_manager")
        row.with_user(manager).write({"backend_id": other_backend.id})
        self.assertEqual(row.backend_id, other_backend)

    def test_cancelled_booking_touches_nothing(self):
        booking = self._booking(9112, [_addon("Parking", 3, 24.0)])
        binding = self._import(booking)
        folio = binding.odoo_id
        service = self._service(folio, "parking")
        state = binding.channel_extras_state
        # Not even a savepoint is opened for a cancelled folio
        with mock.patch.object(type(binding), "_sync_channel_extras_safe") as sync:
            self._import(dict(booking, status=5, addons_list=[]))
        sync.assert_not_called()
        self.assertEqual(folio.state, "cancel")
        self.assertTrue(service.exists())
        self.assertEqual(service.service_line_ids.mapped("cancel_discount"), [100] * 3)
        self.assertEqual(binding.channel_extras_state, state)
        # Confirmed again: the extras count again, nothing is duplicated
        self._import(booking)
        self.assertNotEqual(folio.state, "cancel")
        self.assertEqual(self._services(folio), service)
        self.assertAlmostEqual(folio.amount_total, 3 * NIGHT_PRICE + 24.0)

    def test_booking_without_extras_skips_the_sync(self):
        binding_model = type(self.env["channel.wubook.pms.folio"])
        with mock.patch.object(binding_model, "_sync_channel_extras_safe") as sync:
            binding = self._import(self._booking(9151, []))
        sync.assert_not_called()
        self.assertEqual(binding.channel_extras["items"], [])
        self.assertFalse(binding.channel_extras_state)

    def test_unknown_extra_waits_for_a_product(self):
        booking = self._booking(9113, [_addon("Wine & Cheese", 2, 30.0)])
        binding = self._import(booking)
        folio = binding.odoo_id
        row = self._row("Wine & Cheese")
        self.assertTrue(row)
        self.assertFalse(row.product_id)
        self.assertEqual(row.action, "service")
        self.assertFalse(self._services(folio))
        self.assertEqual(self._state(binding, "wine cheese"), "pending")
        notes = self._extras_notes(folio, "without product")
        self.assertEqual(len(notes), 1)
        self.assertIn("Wine & Cheese", notes[0])
        # Imported again while still pending: no new note
        self._import(booking)
        self.assertEqual(len(self._extras_notes(folio, "without product")), 1)
        # A product is assigned and applied to the pending folios
        row.product_id = self.boat_trip
        result = row.action_apply_to_pending_folios()
        self.assertEqual(result["params"]["type"], "success")
        self.assertIn("1 of 1", result["params"]["message"])
        service = self._service(folio, "wine cheese")
        self.assertEqual(service.product_id, self.boat_trip)
        self.assertEqual(self._lines(service), [(3, 2, 15.0)])
        self.assertEqual(row.service_count, 1)
        self.assertEqual(self._state(binding, "wine cheese"), "applied")
        # Nothing left to apply
        result = row.action_apply_to_pending_folios()
        self.assertIn("0 of 0", result["params"]["message"])

    def test_fallback_product(self):
        self.backend.extra_fallback_product_id = self.fallback
        binding = self._import(self._booking(9114, [_addon("Canoe trip", 2, 50.0)]))
        folio = binding.odoo_id
        service = self._service(folio, "canoe trip")
        self.assertEqual(service.product_id, self.fallback)
        self.assertEqual(service.name, "Channel extra: Canoe trip")
        self.assertEqual(self._lines(service), [(3, 2, 25.0)])
        # The extra is still pending in the map until it gets its own product
        self.assertFalse(self._row("Canoe trip").product_id)
        self.assertFalse(self._extras_notes(folio, "without product"))

    def test_ignored_extra(self):
        self._row("Boat ride").action = "ignore"
        self._row("cradle").active = False
        binding = self._import(
            self._booking(9115, [_addon("Boat ride", 2, 0.0), _addon("cradle", 3, 30)])
        )
        folio = binding.odoo_id
        self.assertFalse(self._services(folio))
        self.assertEqual(self._state(binding, "boat ride"), "ignored")
        self.assertEqual(self._state(binding, "cradle"), "ignored")
        self.assertFalse(self._notes(folio))

    def test_free_package_extra_is_imported_at_zero(self):
        binding = self._import(self._booking(9116, [_addon("Boat ride", 2, 0.0)]))
        service = self._service(binding.odoo_id, "boat ride")
        self.assertEqual(self._lines(service), [(3, 2, 0.0)])

    def test_extra_already_added_by_hand(self):
        booking = self._booking(9117, [_addon("Late checkout", 1, 15.0)])
        binding = self._import(booking)
        folio = binding.odoo_id
        late_checkout = self.env["product.product"].create(
            self._service_vals("Late checkout", 15.0)
        )
        # Reception typed it by hand while the extra had no product
        self.env["pms.service"].create(
            {
                "product_id": late_checkout.id,
                "reservation_id": folio.reservation_ids.id,
            }
        )
        row = self._row("Late checkout")
        row.product_id = late_checkout
        result = row.action_apply_to_pending_folios()
        self.assertIn("0 of 1", result["params"]["message"])
        self.assertFalse(self._services(folio))
        self.assertEqual(self._state(binding, "late checkout"), "manual")
        self.assertEqual(len(self._extras_notes(folio, "added by hand")), 1)
        # It stays with reception from then on
        self._import(booking)
        self.assertFalse(self._services(folio))
        self.assertEqual(len(self._extras_notes(folio, "added by hand")), 1)

    def test_split_booking_creates_nothing(self):
        booking = self._booking(
            9118,
            [_addon("Parking", 4, 32.0)],
            channel_data={
                "addons_embedded": True,
                "split_reservation": {"n_stream": 2, "tot_stream": 2},
            },
        )
        binding = self._import(booking)
        folio = binding.odoo_id
        self.assertFalse(self._services(folio))
        self._import(booking)
        notes = self._extras_notes(folio, "split this booking into 2 folios")
        self.assertEqual(len(notes), 1)
        self.assertIn("Parking", notes[0])

    def test_other_currency_creates_nothing(self):
        booking = self._booking(9119, [_addon("Parking", 3, 24.0)], currency="XYZ")
        binding = self._import(booking)
        self._import(booking)
        folio = binding.odoo_id
        self.assertFalse(self._services(folio))
        notes = self._extras_notes(folio, "are in XYZ")
        self.assertEqual(len(notes), 1)
        self.assertIn("24.00 XYZ", notes[0])

    def test_daily_limit_only_fails_that_extra(self):
        self.parking.with_context(property=self.pms_property.id).daily_limit = 1
        taken = self._import(self._booking(9120, [_addon("Parking", 3, 24.0)]))
        self.assertTrue(self._service(taken.odoo_id, "parking"))
        binding = self._import(
            self._booking(9121, [_addon("Parking", 3, 24.0), _addon("cradle", 3, 30.0)])
        )
        folio = binding.odoo_id
        self.assertTrue(folio.reservation_ids)
        self.assertFalse(self._service(folio, "parking"))
        self.assertTrue(self._service(folio, "cradle"))
        self.assertEqual(self._state(binding, "parking"), "failed")
        notes = self._extras_notes(folio, "Channel extra Parking not imported")
        self.assertEqual(len(notes), 1)
        self.assertIn("limit exceeded", notes[0])
        # Once there is room it can be applied
        self._service(taken.odoo_id, "parking").unlink()
        self._row("Parking").action_apply_to_pending_folios()
        self.assertTrue(self._service(folio, "parking"))

    def test_taxes_must_be_included(self):
        self._row("Boat ride").product_id = self.untaxed
        binding = self._import(self._booking(9122, [_addon("Boat ride", 2, 40.0)]))
        folio = binding.odoo_id
        self.assertFalse(self._services(folio))
        self.assertEqual(self._state(binding, "boat ride"), "failed")
        self.assertEqual(len(self._extras_notes(folio, "are not included")), 1)

    def test_several_rooms_use_the_first_one(self):
        binding = self._import(
            self._booking(9123, [_addon("Parking", 6, 48.0)], rooms=2)
        )
        folio = binding.odoo_id
        self.assertEqual(len(folio.reservation_ids), 2)
        first = folio.reservation_ids.sorted(lambda r: (r.checkin, r.id))[0]
        service = self._service(folio, "parking")
        self.assertEqual(service.reservation_id, first)
        self.assertEqual(self._lines(service), [(0, 2, 8.0), (1, 2, 8.0), (2, 2, 8.0)])

    def test_extras_added_on_board_go_on_the_current_date(self):
        """Once the guests are on board, pms charges the services that are not
        per day on the current date of the hotel, not on the departure date,
        and so do the extras the channel adds or changes then."""
        # 23:30 UTC is already the next day in the hotel
        with freeze_time(datetime.combine(date.today(), time(23, 30))):
            self.env.user.tz = "UTC"
            self.pms_property.tz = "Europe/Madrid"
            hotel_today = date.today() + timedelta(days=1)
            checkin = date.today() - timedelta(days=1)
            binding = self._import(self._booking(9146, [], checkin=checkin))
            folio = binding.odoo_id
            self._put_on_board(folio)
            addons = [_addon("Boat ride", 2, 40.0), _addon("Parking", 3, 24.0)]
            self._import(self._booking(9146, addons, checkin=checkin))
            boat_ride = self._service(folio, "boat ride")
            self.assertEqual(boat_ride.service_line_ids.mapped("date"), [hotel_today])
            # The per day ones are still spread over the nights
            self.assertEqual(
                self._lines(self._service(folio, "parking")),
                [(0, 1, 8.0), (1, 1, 8.0), (2, 1, 8.0)],
            )
            addons[0] = _addon("Boat ride", 2, 50.0)
            self._import(self._booking(9146, addons, checkin=checkin))
            self.assertEqual(
                [
                    (line.date, line.day_qty, line.price_unit)
                    for line in boat_ride.service_line_ids
                ],
                [(hotel_today, 2, 25.0)],
            )

    # what must not change for whoever does not use it

    def _assert_nothing_written(self, binding):
        self.assertTrue(binding.odoo_id.reservation_ids)
        self.assertFalse(self._services(binding.odoo_id))
        self.assertFalse(binding.channel_extras)
        self.assertFalse(binding.channel_extras_state)
        self.assertFalse(self._row("Some extra"))

    def test_channel_without_extras_writes_nothing(self):
        booking = self._booking(
            9124,
            [_addon("Parking", 3, 24.0), _addon("Some extra", 1, 5.0)],
            id_channel=2,
        )
        self._assert_nothing_written(self._import(booking))

    def test_backend_without_start_writes_nothing(self):
        self.backend.extras_import_start = False
        booking = self._booking(9125, [_addon("Some extra", 1, 5.0)])
        self._assert_nothing_written(self._import(booking))

    def test_folio_older_than_start_writes_nothing(self):
        self.backend.extras_import_start = False
        booking = self._booking(9126, [_addon("Some extra", 1, 5.0)])
        binding = self._import(booking)
        # Extras are enabled after the folio was created
        self.backend.extras_import_start = binding.odoo_id.create_date + timedelta(
            seconds=1
        )
        self._assert_nothing_written(self._import(booking))

    def test_start_in_the_future_writes_nothing(self):
        self.backend.extras_import_start = fields.Datetime.now() + timedelta(days=1)
        booking = self._booking(9127, [_addon("Some extra", 1, 5.0)])
        self._assert_nothing_written(self._import(booking))

    # it never fails the import

    def test_errors_do_not_fail_the_import(self):
        booking = self._booking(9128, [_addon("Parking", 3, 24.0)])
        with (
            mock.patch(SYNC_PATH, side_effect=UserError("Something odd")),
            mute_logger(SYNC_MODULE),
        ):
            binding = self._import(booking)
        folio = binding.odoo_id
        self.assertTrue(folio.reservation_ids)
        self.assertFalse(self._services(folio))
        notes = self._extras_notes(folio, "Channel extras could not be imported")
        self.assertEqual(len(notes), 1)
        self.assertIn("Something odd", notes[0])
        with (
            mock.patch(SYNC_PATH, side_effect=KeyError("items")),
            mute_logger(SYNC_MODULE),
        ):
            self._import(booking)
        self.assertEqual(
            len(self._extras_notes(folio, "Channel extras could not be imported")), 2
        )
        # Back to normal, the extras are imported
        self._import(booking)
        self.assertTrue(self._service(folio, "parking"))

    def test_concurrency_errors_retry_the_import(self):
        """Database concurrency errors and RetryableJobError still fail the
        import, so that queue_job retries it, from the sync and the mapper."""
        booking = self._booking(9129, [_addon("Parking", 3, 24.0)])
        for path in (SYNC_PATH, f"{MAPPER_MODULE}.read_channel_extras"):
            for error in ERRORS_TO_RETRY:
                with (
                    self.subTest(path=path, error=error),
                    mock.patch(path, side_effect=error),
                    self.assertRaises(type(error)),
                ):
                    self._import(booking)

    def test_unrelated_failures_still_fail_the_import(self):
        """The work the import leaves pending when the extras start (here a
        precommit hook) is not taken for a failure of the extras: it fails the
        import, as it did before, instead of ending in a folio note."""
        binding_model = type(self.env["channel.wubook.pms.folio"])
        to_sync = binding_model._channel_extras_to_sync

        def unrelated_failure():
            raise ValueError("unrelated failure")

        def to_sync_after_other_work(binding, folio):
            binding.env.cr.precommit.add(unrelated_failure)
            return to_sync(binding, folio)

        booking = self._booking(9133, [_addon("Parking", 3, 24.0)])
        with (
            mock.patch.object(
                binding_model, "_channel_extras_to_sync", to_sync_after_other_work
            ),
            self.assertRaises(ValueError) as caught,
        ):
            self._import(booking)
        self.assertEqual(str(caught.exception), "unrelated failure")

    def test_unreadable_extras_do_not_fail_the_import(self):
        booking = self._booking(9130, [_addon("Parking", 3, 24.0)])
        with (
            mock.patch(
                f"{MAPPER_MODULE}.read_channel_extras",
                side_effect=ValueError("odd payload"),
            ),
            mute_logger(MAPPER_MODULE),
        ):
            binding = self._import(booking)
        self.assertTrue(binding.odoo_id.reservation_ids)
        self.assertFalse(binding.channel_extras)

    def test_map_row_created_meanwhile(self):
        """Another import creating the same row does not fail this one."""
        extras = self.env["channel.wubook.backend.extra"]
        with mock.patch.object(
            type(extras),
            "create",
            side_effect=psycopg2.IntegrityError("duplicate key value"),
        ):
            binding = self._import(self._booking(9131, [_addon("Some extra", 1, 5.0)]))
        self.assertEqual(self._state(binding, "some extra"), "pending")

    def test_map_rows_are_unique_per_backend(self):
        extras = self.env["channel.wubook.backend.extra"]
        with mute_logger("odoo.sql_db"), self.assertRaises(psycopg2.IntegrityError):
            with self.env.cr.savepoint():
                extras.create({"backend_id": self.backend.id, "name": "parking. "})
        self.assertNotEqual(normalize_extra_name("Parking 2"), "parking")
        extras.create({"backend_id": self.backend.id, "name": "Parking 2"})

    def test_notes_in_the_hotel_language(self):
        self.env["res.lang"]._activate_lang("es_ES")
        self.pms_property.lang = "es_ES"
        binding = self._import(self._booking(9132, [_addon("Canoe trip", 2, 50.0)]))
        notes = self._extras_notes(binding.odoo_id, "Extras del canal sin producto")
        self.assertEqual(len(notes), 1)
        self.assertIn("Canoe trip (2 uds.", notes[0])
        self.assertIn("50,00", notes[0])

    def test_pms_errors_in_the_hotel_language(self):
        """The pms errors the notes quote are in the hotel language as well,
        e.g. the date of the daily limit of a product."""
        self.env["res.lang"]._activate_lang("es_ES")
        self.pms_property.lang = "es_ES"
        self.parking.with_context(property=self.pms_property.id).daily_limit = 1
        # After the 12th, so that day/month does not read as month/day
        checkin = self.checkin.replace(day=20)
        addons = [_addon("Parking", 1, 8.0)]
        self._import(self._booking(9147, addons, nights=1, checkin=checkin))
        binding = self._import(self._booking(9148, addons, nights=1, checkin=checkin))
        notes = self._extras_notes(binding.odoo_id, "Extra del canal Parking")
        self.assertEqual(len(notes), 1)
        self.assertIn(checkin.strftime("%d/%m/%Y"), notes[0])
