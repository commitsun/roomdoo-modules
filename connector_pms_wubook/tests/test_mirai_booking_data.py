# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""A Mirai booking through the whole folio import, with the three features
that take what the channel passes through Wubook working together: its
extras become services, what the guest paid online is registered as a
payment and the loyalty club membership goes on top of the folio internal
notes.

The bookings are synthetic but keep the shape of what Wubook returns for the
Mirai booking engine. They go through the real folio importer
(``importer.run``) as the adapter leaves them, and the adapter answers from
them for the old code of a modified booking: nothing reaches Wubook.
"""

import contextlib
import copy
import xmlrpc.client
from datetime import date, timedelta
from unittest import mock

from odoo import fields
from odoo.tests.common import tagged
from odoo.tools import html2plaintext

from odoo.addons.component.tests.common import TransactionComponentCase
from odoo.addons.connector_pms_wubook.models.pms_folio.channel_booking import (
    MIRAI_CHANNEL_ID,
)
from odoo.addons.connector_pms_wubook.models.pms_folio.channel_notes import (
    MIRAI_NOTE_PREFIX,
)

# Wubook ids of the room and of the rate plan of the bookings
ROOM_ID = 1
RATE_ID = 1
NIGHT_PRICE = 100.0
# How Wubook writes the dates of a booking
WUBOOK_DATE = "%d/%m/%Y"
# The loyalty club status as Mirai writes it (the member id is synthetic)
CLUB_MEMBER = (
    "joinsclub: EXISTING - source: RESERVATION - idMember: 1000000000000000001"
)
# What reception reads, in the language of the hotel
CLUB_LINE_ES = MIRAI_NOTE_PREFIX + "Socio del club de fidelización"
PAYMENT_NOTE_ES = "registrado automáticamente"


def _addon(name, req, price):
    """An extra as Mirai sends it: ``price`` is the total of the line and
    ``req`` its total quantity."""
    return {"name": name, "req": req, "price": price, "descr": ""}


@tagged("post_install", "-at_install")
class TestMiraiBookingData(TransactionComponentCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("es_ES")
        # A company with a chart of accounts, so that payments are posted
        cls.company = cls.env.ref("base.main_company")
        cls.currency = cls.company.currency_id
        cls.bank_journal = cls.env["account.journal"].search(
            [("type", "=", "bank"), ("company_id", "=", cls.company.id)], limit=1
        )
        cls.payment_method_line = cls.bank_journal.inbound_payment_method_line_ids[:1]
        # The channel amounts are what the guest pays, taxes included
        cls.tax_included = cls.env["account.tax"].create(
            {
                "name": "Mirai Data VAT 10% included",
                "amount": 10.0,
                "amount_type": "percent",
                "type_tax_use": "sale",
                "price_include": True,
                "company_id": cls.company.id,
                "country_id": (
                    cls.company.account_fiscal_country_id or cls.env.ref("base.es")
                ).id,
            }
        )
        availability_plan = cls.env["pms.availability.plan"].create(
            {"name": "Mirai Data Plan"}
        )
        cls.pricelist = cls.env["product.pricelist"].create(
            {
                "name": "Mirai Data Rate",
                "currency_id": cls.currency.id,
                "availability_plan_id": availability_plan.id,
                "is_pms_available": True,
            }
        )
        cls.pms_property = cls.env["pms.property"].create(
            {
                "name": "Mirai Data Hotel",
                "company_id": cls.company.id,
                "default_pricelist_id": cls.pricelist.id,
                "tz": "Europe/Madrid",
                "lang": "es_ES",
            }
        )
        room_type_class = cls.env["pms.room.type.class"].create(
            {"name": "Mirai Data Class", "default_code": "MDC"}
        )
        cls.room_type = cls.env["pms.room.type"].create(
            {
                "name": "Mirai Data Double",
                "default_code": "MDD",
                "class_id": room_type_class.id,
                "product_id": cls._service_product("Mirai Data Double", 100.0).id,
                "pms_property_ids": [(6, 0, cls.pms_property.ids)],
            }
        )
        for number in range(1, 3):
            cls.env["pms.room"].create(
                {
                    "name": f"Mirai Data {number}",
                    "room_type_id": cls.room_type.id,
                    "pms_property_id": cls.pms_property.id,
                    "capacity": 2,
                }
            )
        sale_channel = cls.env["pms.sale.channel"].create(
            {"name": "Mirai Data Channel", "channel_type": "indirect"}
        )
        cls.agency = cls.env["res.partner"].create(
            {
                "name": "Mirai Data Engine",
                "is_agency": True,
                "sale_channel_id": sale_channel.id,
                "property_product_pricelist": cls.pricelist.id,
            }
        )
        backend_type = cls.env["channel.wubook.backend.type"].create(
            {
                "name": "Mirai Data Type",
                "model_type_id": cls.env.ref(
                    "connector_pms_wubook.model_channel_wubook_backend_type"
                ).id,
                "direct_channel_type_id": cls.env.ref(
                    "connector_pms_wubook.main_pms_sale_channel_wubook"
                ).id,
                # The OTA mapping of Mirai: the club line in the notes and the
                # extras as services
                "ota_ids": [
                    (
                        0,
                        0,
                        {
                            "wubook_ota": MIRAI_CHANNEL_ID,
                            "agency_id": cls.agency.id,
                            "import_channel_notes": True,
                            "import_extras": True,
                        },
                    )
                ],
            }
        )
        cls.mirai_ota = backend_type.ota_ids
        cls.backend = cls.env["channel.wubook.backend"].create(
            {
                "name": "Mirai Data Backend",
                "pms_property_id": cls.pms_property.id,
                "backend_type_id": backend_type.parent_id.id,
                "username": "X",
                "password": "X",
                "property_code": "X",
                "pkey": "X",
                "pricelist_external_id": RATE_ID,
                "wubook_payment_method_line_id": cls.payment_method_line.id,
                # The extras of the folios created from now on
                "extras_import_start": fields.Datetime.now() - timedelta(days=1),
            }
        )
        # The payment mapping of Mirai registers what the guest paid online
        cls.payment_mapping = cls.env["wubook.backend.journal.ota"].create(
            {
                "backend_id": cls.backend.id,
                "agency_id": cls.agency.id,
                "journal_id": cls.bank_journal.id,
                "payment_method_line_id": cls.payment_method_line.id,
                "register_channel_payments": True,
            }
        )
        # The room and the rate plan of the bookings are already imported, so
        # the imports have no dependency to read from Wubook
        for model, record, external_id in (
            ("channel.wubook.pms.room.type", cls.room_type, ROOM_ID),
            ("channel.wubook.product.pricelist", cls.pricelist, RATE_ID),
        ):
            cls.env[model].with_context(connector_no_export=True).create(
                {
                    "odoo_id": record.id,
                    "backend_id": cls.backend.id,
                    "external_id": external_id,
                }
            )
        cls.parking = cls._service_product("Mirai Data Parking", 15.0, per_day=True)
        cls.breakfast = cls._service_product(
            "Mirai Data Breakfast",
            12.0,
            per_day=True,
            per_person=True,
            consumed_on="after",
        )
        for name, product in (("Parking", cls.parking), ("Breakfast", cls.breakfast)):
            cls.env["channel.wubook.backend.extra"].create(
                {"backend_id": cls.backend.id, "name": name, "product_id": product.id}
            )
        cls.checkin = date.today() + timedelta(days=30)

    @classmethod
    def _service_product(cls, name, price, **flags):
        return cls.env["product.product"].create(
            {
                "name": name,
                "type": "service",
                "list_price": price,
                "taxes_id": [(6, 0, cls.tax_included.ids)],
                **flags,
            }
        )

    def setUp(self):
        super().setUp()
        # The bookings Wubook returns, by code (see _wubook)
        self.versions = {}
        # Nothing here may reach Wubook
        network = mock.patch.object(
            xmlrpc.client, "Server", side_effect=AssertionError("Wubook reached")
        )
        server = network.start()
        self.addCleanup(server.assert_not_called)
        self.addCleanup(network.stop)

    # helpers

    def _booking(self, code, nights=2, extras=True, paid=True, club=True, **values):
        """A Mirai booking as fetch_booking returns it: a room for 2 guests,
        ``nights`` nights from ``self.checkin``.

        Unless left out, it carries what Mirai passes through Wubook: a
        parking and the breakfasts as extras, outside the booking amount, the
        whole booking paid online (amount plus extras) and a guest who is a
        member of the loyalty club.
        """
        days = [self.checkin + timedelta(days=night) for night in range(nights)]
        amount = NIGHT_PRICE * nights
        addons = []
        if extras:
            addons = [
                _addon("Parking", nights, 15.0 * nights),
                _addon("Breakfast", 2 * nights, 24.0 * nights),
            ]
        currency = self.currency.name
        ancillary = {"Detected Board": "nb"}
        if paid:
            total = amount + sum(addon["price"] for addon in addons)
            ancillary["Paid amount"] = f"{total:.2f} {currency}"
            ancillary["Due amount"] = f"0.00 {currency}"
        if club:
            ancillary["ClubStatus"] = CLUB_MEMBER
        booking = {
            "reservation_code": code,
            "channel_reservation_code": f"MIRAI-{code}",
            "id_channel": MIRAI_CHANNEL_ID,
            "status": 1,
            "was_modified": 0,
            "modified_reservations": [],
            "amount": amount,
            "currency": currency,
            "payment_gateway_fee": "",
            "addons_list": addons,
            "channel_data": {"addons_embedded": True},
            "ancillary": ancillary,
            "date_received_time": "01/06/2026 10:00:00",
            "date_arrival": days[0].strftime(WUBOOK_DATE),
            "date_departure": (days[-1] + timedelta(days=1)).strftime(WUBOOK_DATE),
            "arrival_hour": "--",
            "men": 2,
            "children": 0,
            "rooms": str(ROOM_ID),
            "boards": "",
            "dayprices": {str(ROOM_ID): [NIGHT_PRICE] * nights},
            "customer_name": "Test",
            "customer_surname": "Guest",
            "customer_mail": "",
            "customer_phone": "",
            "customer_notes": "",
            "customer_language_iso": "es",
            "booked_rooms": [
                {
                    "room_id": ROOM_ID,
                    "ancillary": {"adults": 2, "children": 0},
                    "roomdays": [
                        {
                            "day": day.strftime(WUBOOK_DATE),
                            "price": NIGHT_PRICE,
                            "rate_id": RATE_ID,
                            "ancillary": {},
                        }
                        for day in days
                    ],
                }
            ],
        }
        booking.update(values)
        return booking

    @contextlib.contextmanager
    def _wubook(self):
        """Stand in for the Wubook API: fetch_booking returns the last
        version of a code in ``self.versions``. Yield the codes fetched."""
        fetched = []

        def fetch(adapter, funcname, *args, pms_property=True):
            self.assertEqual(funcname, "fetch_booking")
            fetched.append(args[0])
            return [copy.deepcopy(self.versions[args[0]])]

        with self.backend.work_on("channel.wubook.pms.folio") as work:
            adapter = work.component(usage="backend.adapter")
        with mock.patch.object(type(adapter), "_exec", fetch):
            yield fetched

    def _import(self, booking):
        """Import ``booking`` as a push of its code from Wubook does, after
        making it the version Wubook returns for its code. Return its folio
        binding and the codes read from Wubook meanwhile."""
        code = booking["reservation_code"]
        self.versions[code] = booking
        with self.backend.work_on("channel.wubook.pms.folio") as work:
            adapter = work.component(usage="backend.adapter")
            data = [copy.deepcopy(booking)]
            adapter._format_folio_data(data)
            adapter._reorg_folio_data(data)
            importer = work.component_by_name("channel.wubook.pms.folio.importer")
            with self._wubook() as fetched:
                importer.run(code, external_data=data[0])
        return self._binding(code), fetched

    def _binding(self, code):
        return self.env["channel.wubook.pms.folio"].search(
            [("backend_id", "=", self.backend.id), ("external_id", "=", str(code))]
        )

    def _extras(self, folio):
        """The services the channel extras became: {key: (reservation,
        product, lines)}, the lines as (night, quantity, unit price) counted
        from the checkin of the reservation."""
        result = {}
        for service in folio.service_ids.filtered("channel_extra_key"):
            reservation = service.reservation_id
            result[service.channel_extra_key] = (
                reservation,
                service.product_id,
                [
                    (
                        (line.date - reservation.checkin).days,
                        line.day_qty,
                        line.price_unit,
                    )
                    for line in service.service_line_ids.sorted("date")
                ],
            )
        return result

    def _payments(self, folio):
        return folio.payment_ids.filtered(lambda payment: payment.state == "posted")

    def _messages(self, folio):
        return self.env["mail.message"].search(
            [("model", "=", "pms.folio"), ("res_id", "=", folio.id)]
        )

    def _notes(self, folio, text):
        """Messages of the folio containing ``text``."""
        return [
            body
            for body in (
                html2plaintext(message.body).replace("\xa0", " ")
                for message in self._messages(folio)
                if message.body
            )
            if text in body
        ]

    def _seen_by_staff(self, folio):
        """What reception sees of ``folio``: totals, services, payments,
        internal notes and messages (not their text, which names records)."""
        return {
            "state": folio.state,
            "amount_total": folio.amount_total,
            "pending_amount": folio.pending_amount,
            "internal_comment": folio.internal_comment,
            "services": sorted(
                (service.product_id.id, service.product_qty, service.price_total)
                for service in folio.service_ids
            ),
            "payments": sorted(folio.payment_ids.mapped("amount")),
            "messages": sorted(
                (
                    message.message_type,
                    message.subtype_id.id,
                    bool(message.body),
                    tuple(sorted(message.tracking_value_ids.mapped("field.name"))),
                )
                for message in self._messages(folio)
            ),
        }

    def _folio_form_for(self, login, *groups):
        """The folio form as a user of the hotel with ``groups`` gets it."""
        user = self.env["res.users"].create(
            {
                "name": login,
                "login": login,
                "company_id": self.company.id,
                "company_ids": [(6, 0, self.company.ids)],
                "pms_property_id": self.pms_property.id,
                "pms_property_ids": [(6, 0, self.pms_property.ids)],
                "groups_id": [
                    (6, 0, [self.env.ref(group).id for group in groups]),
                ],
            }
        )
        return self.env["pms.folio"].with_user(user).get_view(view_type="form")

    # tests

    def test_booking_with_everything_enabled(self):
        booking = self._booking(1790000001)
        binding, fetched = self._import(booking)
        self.assertFalse(fetched)
        folio = binding.odoo_id
        reservation = folio.reservation_ids
        self.assertEqual(len(reservation), 1)
        self.assertEqual(folio.agency_id, self.agency)
        # The extras are services of the room, placed as pms places them
        self.assertEqual(
            self._extras(folio),
            {
                "parking": (reservation, self.parking, [(0, 1, 15.0), (1, 1, 15.0)]),
                "breakfast": (
                    reservation,
                    self.breakfast,
                    [(1, 2, 12.0), (2, 2, 12.0)],
                ),
            },
        )
        self.assertEqual(
            folio.service_ids, folio.service_ids.filtered("channel_extra_key")
        )
        # The folio adds up to what the guest pays: the rooms plus the extras
        self.assertAlmostEqual(folio.amount_total, booking["amount"] + 30.0 + 48.0)
        # Paid online in full: one payment of the amount plus the extras
        payment = self._payments(folio)
        self.assertEqual(payment.mapped("amount"), [278.0])
        self.assertEqual(payment.payment_method_line_id, self.payment_method_line)
        self.assertEqual(binding.channel_payment_ids, payment)
        self.assertEqual(binding.channel_paid_registered, 278.0)
        self.assertAlmostEqual(folio.pending_amount, 0.0)
        # The loyalty club line on top of the internal notes, in Spanish
        self.assertEqual(folio.internal_comment, CLUB_LINE_ES)
        # The only note is the one of the payment: the extras were in the
        # folio before the payment was registered, so the amount paid does
        # not exceed its total, and nothing failed
        notes = self._notes(folio, PAYMENT_NOTE_ES)
        self.assertEqual(len(notes), 1)
        self.assertIn("278,00", notes[0])
        self.assertNotIn("exceeds", binding.channel_payment_notes or {})
        self.assertFalse(binding.channel_extras_state["notes"])
        self.assertFalse(self._notes(folio, "[Mirai]"))
        # Imported again (push, cron, resync...): nothing changes
        seen = self._seen_by_staff(folio)
        services = folio.service_ids
        lines = services.service_line_ids
        self._import(booking)
        self.assertEqual(self._seen_by_staff(folio), seen)
        self.assertEqual(folio.service_ids, services)
        self.assertEqual(folio.service_ids.service_line_ids, lines)
        self.assertEqual(binding.channel_paid_registered, 278.0)

    def test_modification_paid_online(self):
        """The guest adds a night and pays it online. Wubook keeps the old
        code as modified and the new code names it, so that the same folio
        gets the new stay, its extras, the difference paid and its notes."""
        old_code, new_code = 1790000101, 1790000102
        binding, fetched = self._import(self._booking(old_code))
        folio = binding.odoo_id
        old_reservation = folio.reservation_ids
        old_services = folio.service_ids
        self.assertEqual(self._payments(folio).mapped("amount"), [278.0])
        # The old code, modified, is discarded
        modified = dict(self.versions[old_code], status=5, was_modified=1)
        self.assertEqual(self._import(modified)[0], binding)
        self.assertEqual(folio.state, "confirm")
        # The new code, one night longer
        booking = self._booking(new_code, nights=3, modified_reservations=[old_code])
        new_binding, fetched = self._import(booking)
        self.assertEqual(new_binding, binding)
        self.assertEqual(binding.odoo_id, folio)
        self.assertFalse(self._binding(old_code))
        self.assertFalse(fetched)
        self.assertEqual(old_reservation.state, "cancel")
        self.assertEqual(old_reservation.cancelled_reason, "modified")
        reservation = folio.reservation_ids - old_reservation
        self.assertEqual(reservation.checkout - reservation.checkin, timedelta(days=3))
        # The extras moved to the new stay, as the channel sends them now
        self.assertFalse(old_services.exists())
        self.assertEqual(
            self._extras(folio),
            {
                "parking": (
                    reservation,
                    self.parking,
                    [(0, 1, 15.0), (1, 1, 15.0), (2, 1, 15.0)],
                ),
                "breakfast": (
                    reservation,
                    self.breakfast,
                    [(1, 2, 12.0), (2, 2, 12.0), (3, 2, 12.0)],
                ),
            },
        )
        self.assertAlmostEqual(folio.amount_total, 300.0 + 45.0 + 72.0)
        # Only the difference is registered
        self.assertEqual(sorted(self._payments(folio).mapped("amount")), [139.0, 278.0])
        self.assertEqual(binding.channel_paid_registered, 417.0)
        self.assertEqual(binding.channel_payment_ids, self._payments(folio))
        self.assertAlmostEqual(folio.pending_amount, 0.0)
        self.assertEqual(len(self._notes(folio, PAYMENT_NOTE_ES)), 2)
        self.assertNotIn("exceeds", binding.channel_payment_notes or {})
        self.assertFalse(binding.channel_extras_state["notes"])
        # The club line stays as it was
        self.assertEqual(folio.internal_comment, CLUB_LINE_ES)
        # Imported again: its dependencies read the old code from Wubook,
        # which is discarded, and nothing changes
        seen = self._seen_by_staff(folio)
        services = folio.service_ids
        new_binding, fetched = self._import(booking)
        self.assertEqual(fetched, [old_code])
        self.assertEqual(new_binding, binding)
        self.assertEqual(self._seen_by_staff(folio), seen)
        self.assertEqual(folio.service_ids, services)

    def test_nothing_new_when_disabled(self):
        """With the three features disabled, a Mirai booking is imported as if
        it passed none of that data: reception sees the same as for a booking
        without extras, payment nor club status."""
        self.mirai_ota.write({"import_channel_notes": False, "import_extras": False})
        self.backend.extras_import_start = False
        self.payment_mapping.register_channel_payments = False
        rows = self.env["channel.wubook.backend.extra"].search(
            [("backend_id", "=", self.backend.id)]
        )
        booking = self._booking(1790000201)
        # An extra unknown to the backend is not added to its extras either
        booking["addons_list"].append(_addon("Late checkout", 1, 20.0))
        bindings = type(self.env["channel.wubook.pms.folio"])
        called = []

        def spy(name):
            method = getattr(bindings, name)

            def wrapper(binding, *args, **kwargs):
                called.append(name)
                return method(binding, *args, **kwargs)

            return mock.patch.object(bindings, name, wrapper)

        with spy("_get_channel_note_lines"), spy("_sync_channel_extras"), spy(
            "_sync_channel_payments"
        ):
            binding = self._import(booking)[0]
            plain = self._import(
                self._booking(1790000202, extras=False, paid=False, club=False)
            )[0]
        # None of them even started
        self.assertFalse(called)
        folio = binding.odoo_id
        self.assertTrue(folio.reservation_ids)
        self.assertFalse(folio.service_ids)
        self.assertFalse(folio.payment_ids)
        self.assertFalse(folio.internal_comment)
        self.assertAlmostEqual(folio.amount_total, booking["amount"])
        self.assertEqual(self._seen_by_staff(folio), self._seen_by_staff(plain.odoo_id))
        self.assertEqual(
            self.env["channel.wubook.backend.extra"]
            .with_context(active_test=False)
            .search([("backend_id", "=", self.backend.id)]),
            rows,
        )
        # Nothing done is recorded on the binding
        self.assertFalse(binding.channel_extras)
        self.assertFalse(binding.channel_extras_state)
        self.assertFalse(binding.channel_payment_ids)
        self.assertEqual(binding.channel_paid_registered, 0.0)
        self.assertFalse(binding.channel_payment_notes)
        # What the channel reports as paid is kept on the binding, ready for
        # when the channel payments are enabled, but only connector managers
        # see the bindings of a folio
        self.assertEqual(binding.channel_paid_amount, 278.0)
        reception = self._folio_form_for(
            "mirai.data.reception", "base.group_user", "pms.group_pms_user"
        )
        self.assertNotIn("channel_paid_amount", reception["arch"])
        connector_manager = self._folio_form_for(
            "mirai.data.connector",
            "base.group_user",
            "pms.group_pms_user",
            "connector.group_connector_manager",
        )
        self.assertIn("channel_paid_amount", connector_manager["arch"])

    def test_settings_in_spanish(self):
        """The settings of the three features come from es.po in Spanish."""
        self.env["ir.module.module"]._load_module_terms(
            ["connector_pms_wubook"], ["es_ES"], overwrite=True
        )
        for model, field, string in (
            (
                "channel.wubook.backend.type.ota",
                "import_channel_notes",
                "Notas del canal",
            ),
            ("channel.wubook.backend.type.ota", "import_extras", "Importar extras"),
            ("channel.wubook.backend", "extras_import_start", "Importar extras desde"),
            (
                "wubook.backend.journal.ota",
                "register_channel_payments",
                "Registrar pagos del canal",
            ),
        ):
            with self.subTest(field=field):
                description = (
                    self.env[model]
                    .with_context(lang="es_ES")
                    .fields_get([field], ["string"])[field]
                )
                self.assertEqual(description["string"], string)
