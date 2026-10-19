# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""Tests for the registration of what a channel reports as paid online.

The Mirai booking engine passes through Wubook, in the booking ancillary,
how much the guest paid online ("Paid amount") and how much is still due
("Due amount"), and a pending online payment ("BTP"). The bookings below are
synthetic but keep the shape of the real payloads.
"""

import contextlib
import copy
import datetime
import xmlrpc.client
from datetime import date, timedelta
from unittest import mock

import psycopg2
import pytz

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged
from odoo.tools import html2plaintext, mute_logger

from odoo.addons.component.tests.common import TransactionComponentCase
from odoo.addons.queue_job.exception import RetryableJobError

from ..models.pms_folio.channel_payments import (
    channel_payment_values,
    local_to_utc,
    parse_wubook_amount,
    parse_wubook_btp,
)

MIRAI = 197
PAYMENTS_LOGGER = "odoo.addons.connector_pms_wubook.models.pms_folio.channel_payments"
IMPORTER_LOGGER = "odoo.addons.connector_pms_wubook.models.pms_folio.importer"
# How Wubook writes the dates of a booking
WUBOOK_DATE = "%d/%m/%Y"


def mirai_booking(
    paid="0 EUR",
    due="0.00 EUR",
    btp=None,
    split=None,
    status=1,
    reservation_code=1780000001,
    channel_code="TEST-0001",
    was_modified=0,
    modified_reservations=(),
):
    """A Mirai booking as fetch_booking returns it (only synthetic values)."""
    ancillary = {
        "Booked rooms": "1",
        "Room id #1": "1",
        "ClubStatus": "member: NO",
        "Features": "",
        "Channel reservation id": channel_code,
        "Paid amount": paid,
        "Due amount": due,
        "Cancel policy": "",
        "Cancellation policy": "Refundable",
        "Cancellation deadline": "2026-07-01T00:00:00",
        "ChannelHotelId": "100000",
        "Detected Board": "nb",
    }
    if btp:
        ancillary["BTP"] = btp
    channel_data = {"addons_embedded": True}
    if split:
        channel_data["split_reservation"] = {
            "n_stream": split[0],
            "tot_stream": split[1],
        }
    return {
        "id_channel": MIRAI,
        "reservation_code": reservation_code,
        "channel_reservation_code": channel_code,
        "status": status,
        "was_modified": was_modified,
        "modified_reservations": list(modified_reservations),
        "payment_gateway_fee": "",
        "amount": 200.0,
        "currency": "EUR",
        "addons_list": [],
        "channel_data": channel_data,
        "ancillary": ancillary,
    }


def fetched_mirai_booking(checkin, nights=2, price=100.0, **values):
    """The whole booking as fetch_booking returns it, before the adapter
    reorganizes it: ``mirai_booking`` plus the stay and the guest, in a room
    and a rate plan with Wubook id 1 (only synthetic values)."""
    booking = mirai_booking(**values)
    days = [checkin + timedelta(days=night) for night in range(nights)]
    booking.update(
        {
            "amount": price * nights,
            "date_received_time": "01/06/2026 10:00:00",
            "date_arrival": days[0].strftime(WUBOOK_DATE),
            "date_departure": (days[-1] + timedelta(days=1)).strftime(WUBOOK_DATE),
            "arrival_hour": "--",
            "men": 2,
            "children": 0,
            "rooms": "1",
            "boards": "",
            "dayprices": {"1": [price] * nights},
            "customer_name": "Test",
            "customer_surname": "Guest",
            "customer_mail": "",
            "customer_phone": "",
            "customer_notes": "",
            "customer_language_iso": "en",
            "booked_rooms": [
                {
                    "room_id": 1,
                    "ancillary": {"adults": 2, "children": 0},
                    "roomdays": [
                        {
                            "day": day.strftime(WUBOOK_DATE),
                            "price": price,
                            "rate_id": 1,
                            "ancillary": {},
                        }
                        for day in days
                    ],
                }
            ],
        }
    )
    return booking


class TestChannelPaymentParsing(TransactionCase):
    def test_parse_amount(self):
        for text, expected in (
            ("218.03 EUR", (218.03, "EUR")),
            ("0 EUR", (0.0, "EUR")),
            ("0.00 EUR", (0.0, "EUR")),
            ("1986.2 EUR", (1986.2, "EUR")),
            (" 2471.04 EUR ", (2471.04, "EUR")),
        ):
            with self.subTest(text=text):
                self.assertEqual(parse_wubook_amount(text), expected)

    def test_parse_amount_rejects_other_shapes(self):
        """Whatever is not '<amount> <ISO>' is rejected, never guessed."""
        for text in (
            None,
            "",
            "1.234,50 EUR",
            "218,03 EUR",
            "1,234.50 EUR",
            "EUR 218.03",
            "218.03",
            "218.034 EUR",
            "-5.00 EUR",
            "218.03 eur",
            "abc",
            218.03,
        ):
            with self.subTest(text=text):
                self.assertEqual(parse_wubook_amount(text), (None, None))

    def test_parse_btp(self):
        self.assertEqual(
            parse_wubook_btp("BTP|2026-07-03T23:02:48|Pending"),
            datetime.datetime(2026, 7, 3, 23, 2, 48),
        )

    def test_parse_btp_rejects_other_shapes(self):
        for text in (
            None,
            "",
            "Pending",
            "BTP|2026-07-03T23:02:48|Paid",
            "BTP|2026-07-03 23:02:48|Pending",
            "BTP|2026-07-03|Pending",
            "BTP|2026-13-03T23:02:48|Pending",
        ):
            with self.subTest(text=text):
                self.assertIsNone(parse_wubook_btp(text))

    def test_local_to_utc(self):
        local = datetime.datetime(2026, 7, 3, 23, 2, 48)
        # Summer time in Madrid is UTC+2, winter time UTC+1
        self.assertEqual(
            local_to_utc(local, "Europe/Madrid"),
            datetime.datetime(2026, 7, 3, 21, 2, 48),
        )
        self.assertEqual(
            local_to_utc(datetime.datetime(2026, 1, 10, 10, 0), "Europe/Madrid"),
            datetime.datetime(2026, 1, 10, 9, 0),
        )
        # Without a valid time zone the value is taken as UTC
        self.assertEqual(local_to_utc(local, False), local)
        self.assertEqual(local_to_utc(local, "Not/AZone"), local)

    def test_values_prepaid(self):
        """Paid online when booking: paid = amount + extras, nothing due."""
        values = channel_payment_values(
            mirai_booking(paid="262.61 EUR", due="0.00 EUR"), "Europe/Madrid"
        )
        self.assertEqual(
            values,
            {
                "channel_payment_split": False,
                "channel_paid_amount": 262.61,
                "channel_due_amount": 0.0,
                "channel_payment_currency": "EUR",
                "channel_payment_deadline": False,
            },
        )

    def test_values_card_guarantee(self):
        values = channel_payment_values(
            mirai_booking(paid="0 EUR", due="322.00 EUR"), "Europe/Madrid"
        )
        self.assertEqual(values["channel_paid_amount"], 0.0)
        self.assertEqual(values["channel_due_amount"], 322.0)
        self.assertEqual(values["channel_payment_currency"], "EUR")

    def test_values_zero_zero_keeps_last_known(self):
        """0 paid and 0 due carry no information: nothing is overwritten."""
        values = channel_payment_values(
            mirai_booking(paid="0 EUR", due="0.00 EUR"), "Europe/Madrid"
        )
        self.assertEqual(values, {"channel_payment_split": False})

    def test_values_pending_payment(self):
        """The deadline is local time of the hotel, stored in UTC."""
        values = channel_payment_values(
            mirai_booking(btp="BTP|2026-07-03T23:02:48|Pending"), "Europe/Madrid"
        )
        self.assertEqual(
            values,
            {
                "channel_payment_split": False,
                "channel_payment_deadline": datetime.datetime(2026, 7, 3, 21, 2, 48),
            },
        )

    def test_values_split(self):
        values = channel_payment_values(
            mirai_booking(paid="0 EUR", due="322.00 EUR", split=(2, 2)),
            "Europe/Madrid",
        )
        self.assertEqual(values["channel_payment_split"], "2/2")
        self.assertEqual(values["channel_due_amount"], 322.0)

    def test_values_without_paid_amount(self):
        """Channels that do not report it (Booking.com...) map nothing."""
        booking = mirai_booking()
        del booking["ancillary"]["Paid amount"]
        self.assertEqual(channel_payment_values(booking, "UTC"), {})
        booking["ancillary"] = []
        self.assertEqual(channel_payment_values(booking, "UTC"), {})
        del booking["ancillary"]
        self.assertEqual(channel_payment_values(booking, "UTC"), {})

    def test_values_unreadable_amounts(self):
        for paid, due in (
            ("218,03 EUR", "0.00 EUR"),
            ("218.03 EUR", None),
            ("218.03 EUR", "0.00 USD"),
        ):
            booking = mirai_booking(paid=paid, due=due)
            if due is None:
                del booking["ancillary"]["Due amount"]
            with self.subTest(paid=paid, due=due), self.assertLogs(
                PAYMENTS_LOGGER, level="WARNING"
            ):
                self.assertEqual(
                    channel_payment_values(booking, "UTC"),
                    {"channel_payment_split": False},
                )


@tagged("post_install", "-at_install")
class TestFolioChannelPayments(TransactionComponentCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # A company with a chart of accounts, so that payments can be posted
        cls.company = cls.env.ref("base.main_company")
        cls.currency = cls.company.currency_id
        cls.bank_journal = cls.env["account.journal"].search(
            [("type", "=", "bank"), ("company_id", "=", cls.company.id)], limit=1
        )
        cls.payment_method_line = cls.bank_journal.inbound_payment_method_line_ids[:1]
        cls.availability_plan = cls.env["pms.availability.plan"].create(
            {"name": "Channel Payments Plan"}
        )
        cls.pricelist = cls.env["product.pricelist"].create(
            {
                "name": "Channel Payments Pricelist",
                "currency_id": cls.currency.id,
                "availability_plan_id": cls.availability_plan.id,
                "is_pms_available": True,
            }
        )
        cls.pms_property = cls.env["pms.property"].create(
            {
                "name": "Channel Payments Property",
                "company_id": cls.company.id,
                "default_pricelist_id": cls.pricelist.id,
                "tz": "Europe/Madrid",
                "lang": "en_US",
            }
        )
        room_type_class = cls.env["pms.room.type.class"].create(
            {"name": "RTC CP", "default_code": "RTCCP"}
        )
        cls.room_type = cls.env["pms.room.type"].create(
            {
                "name": "RT-CP",
                "default_code": "RTCP",
                "class_id": room_type_class.id,
                "list_price": 100.0,
                "pms_property_ids": [(6, 0, [cls.pms_property.id])],
            }
        )
        for number in range(3):
            cls.env["pms.room"].create(
                {
                    "name": f"Room CP {number}",
                    "room_type_id": cls.room_type.id,
                    "pms_property_id": cls.pms_property.id,
                    "capacity": 2,
                }
            )
        cls.sale_channel = cls.env["pms.sale.channel"].create(
            {"name": "Channel Payments OTA", "channel_type": "indirect"}
        )
        cls.agency = cls.env["res.partner"].create(
            {
                "name": "Test Booking Engine",
                "is_agency": True,
                "sale_channel_id": cls.sale_channel.id,
                "property_product_pricelist": cls.pricelist.id,
            }
        )
        backend_type = cls.env["channel.wubook.backend.type"].create(
            {
                "name": "BT-CP",
                "model_type_id": cls.env.ref(
                    "connector_pms_wubook.model_channel_wubook_backend_type"
                ).id,
                "direct_channel_type_id": cls.env.ref(
                    "connector_pms_wubook.main_pms_sale_channel_wubook"
                ).id,
                "ota_ids": [(0, 0, {"wubook_ota": MIRAI, "agency_id": cls.agency.id})],
            }
        )
        cls.backend = cls.env["channel.wubook.backend"].create(
            {
                "name": "Channel Payments Backend",
                "pms_property_id": cls.pms_property.id,
                "backend_type_id": backend_type.parent_id.id,
                "username": "X",
                "password": "X",
                "property_code": "X",
                "pkey": "X",
                "pricelist_external_id": 1,
                "wubook_payment_method_line_id": cls.payment_method_line.id,
            }
        )
        cls.ota_mapping = cls.env["wubook.backend.journal.ota"].create(
            {
                "backend_id": cls.backend.id,
                "agency_id": cls.agency.id,
                "journal_id": cls.bank_journal.id,
                "payment_method_line_id": cls.payment_method_line.id,
                "register_channel_payments": True,
            }
        )
        # The room and the rate plan of the fetched bookings are already
        # imported, so whole imports have no dependency to read from Wubook
        for model, record in (
            ("channel.wubook.pms.room.type", cls.room_type),
            ("channel.wubook.product.pricelist", cls.pricelist),
        ):
            cls.env[model].with_context(connector_no_export=True).create(
                {"odoo_id": record.id, "backend_id": cls.backend.id, "external_id": 1}
            )
        cls.checkin = date.today() + timedelta(days=30)

    # helpers

    def _amount(self, value):
        """'<amount> <ISO>' as the channel writes it, in the folio currency."""
        return f"{value:.2f} {self.currency.name}"

    def _new_folio(self, channel_code="TEST-0001", external_id=1780000001, days=0):
        reservation = self.env["pms.reservation"].create(
            {
                "pms_property_id": self.pms_property.id,
                "checkin": self.checkin + timedelta(days=days),
                "checkout": self.checkin + timedelta(days=days + 2),
                "partner_name": "Channel guest",
                "agency_id": self.agency.id,
                "sale_channel_origin_id": self.sale_channel.id,
                "room_type_id": self.room_type.id,
                "ota_reservation_code": channel_code,
            }
        )
        folio = reservation.folio_id
        self.assertEqual(folio.agency_id, self.agency)
        self.assertEqual(folio.currency_id, self.currency)
        binding = self.env["channel.wubook.pms.folio"].create(
            {
                "odoo_id": folio.id,
                "backend_id": self.backend.id,
                "external_id": external_id,
            }
        )
        return folio, binding

    def _mapper(self):
        with self.backend.work_on("channel.wubook.pms.folio") as work:
            return work.component(usage="import.mapper")

    def _adapter(self):
        with self.backend.work_on("channel.wubook.pms.folio") as work:
            return work.component(usage="backend.adapter")

    def _importer(self):
        with self.backend.work_on("channel.wubook.pms.folio") as work:
            return work.component_by_name("channel.wubook.pms.folio.importer")

    def _import(self, binding, **booking_values):
        """Write what the mapper maps from the booking and run what the
        importer runs after writing it."""
        booking = mirai_booking(**booking_values)
        mapper = self._mapper()
        values = mapper.channel_payment(booking)
        values.update(mapper.wubook_status(booking))
        binding.write(values)
        self._importer()._after_import(binding)

    def _reorganized(self, booking):
        """What the adapter makes of a fetched booking (Wubook is not called)."""
        bookings = [copy.deepcopy(booking)]
        adapter = self._adapter()
        adapter._format_folio_data(bookings)
        adapter._reorg_folio_data(bookings)
        return bookings[0]

    @contextlib.contextmanager
    def _wubook(self, versions):
        """Stand in for the Wubook API, which is never reached: fetch_booking
        returns the current version of a booking code from ``versions``.
        Yield the list of the booking codes fetched."""
        fetched = []

        def fetch(adapter, funcname, *args, pms_property=True):
            self.assertEqual(funcname, "fetch_booking")
            fetched.append(args[0])
            return [copy.deepcopy(versions[args[0]])]

        with mock.patch.object(
            type(self._adapter()), "_exec", fetch
        ), mock.patch.object(
            xmlrpc.client, "Server", side_effect=AssertionError("Wubook called")
        ):
            yield fetched

    def _run_import(self, versions, code, **booking_values):
        """Import the booking ``code`` as the batch importer does, through
        importer.run() with the reorganized data Wubook returned, after
        making it the current version of ``code`` in ``versions``."""
        versions[code] = fetched_mirai_booking(
            self.checkin, reservation_code=code, **booking_values
        )
        with self._wubook(versions) as fetched:
            self._importer().run(code, external_data=self._reorganized(versions[code]))
        return fetched

    def _binding(self, code):
        return self.env["channel.wubook.pms.folio"].search(
            [("backend_id", "=", self.backend.id), ("external_id", "=", code)]
        )

    def _payments(self, folio):
        return folio.payment_ids.filtered(lambda pay: pay.state == "posted")

    def _posted_amounts(self, folio):
        """Amounts of the posted payments of the folio in the database."""
        return (
            self.env["account.payment"]
            .search([("folio_ids", "in", folio.ids), ("state", "=", "posted")])
            .mapped("amount")
        )

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

    # what is mapped

    def test_mapper_uses_the_property_time_zone(self):
        values = self._mapper().channel_payment(
            mirai_booking(btp="BTP|2026-07-03T23:02:48|Pending")
        )
        self.assertEqual(
            values["channel_payment_deadline"],
            datetime.datetime(2026, 7, 3, 21, 2, 48),
        )

    def test_mapper_never_fails_the_import(self):
        with mock.patch(
            "odoo.addons.connector_pms_wubook.models.pms_folio.mapper_import."
            "channel_payment_values",
            side_effect=TypeError("unexpected"),
        ), mute_logger("odoo.addons.connector_pms_wubook.models.pms_folio"):
            self.assertEqual(self._mapper().channel_payment(mirai_booking()), {})

    def test_folio_mapper_maps_the_channel_payment(self):
        """The mapping is wired in the folio mapper, on creation and on
        every update of the binding."""
        booking = fetched_mirai_booking(
            self.checkin, paid=self._amount(150), due=self._amount(50)
        )
        map_record = self._mapper().map_record(self._reorganized(booking))
        expected = {
            "channel_paid_amount": 150.0,
            "channel_due_amount": 50.0,
            "channel_payment_currency": self.currency.name,
            "channel_payment_deadline": False,
            "channel_payment_split": False,
        }
        for for_create in (True, False):
            with self.subTest(for_create=for_create):
                values = map_record.values(for_create=for_create)
                self.assertEqual(
                    {key: values.get(key, "missing") for key in expected}, expected
                )

    # registration

    def test_prepaid_booking_is_registered_once(self):
        folio, binding = self._new_folio()
        self._import(binding, paid=self._amount(150), due=self._amount(0))
        payment = self._payments(folio)
        self.assertEqual(len(payment), 1)
        self.assertEqual(payment.amount, 150.0)
        self.assertEqual(payment.payment_method_line_id, self.payment_method_line)
        self.assertEqual(payment.payment_type, "inbound")
        self.assertIn(self.agency.name, payment.ref)
        # Today for the hotel
        self.assertEqual(
            payment.date,
            fields.Datetime.now()
            .replace(tzinfo=pytz.utc)
            .astimezone(pytz.timezone("Europe/Madrid"))
            .date(),
        )
        self.assertEqual(binding.channel_payment_ids, payment)
        self.assertEqual(binding.channel_paid_registered, 150.0)
        self.assertEqual(len(self._notes(folio, "registered automatically")), 1)
        # Re-imports of the same version (push, cron, resync) do nothing
        self._import(binding, paid=self._amount(150), due=self._amount(0))
        self.assertEqual(self._payments(folio), payment)
        self.assertEqual(binding.channel_paid_registered, 150.0)
        self.assertEqual(len(self._notes(folio, "registered automatically")), 1)

    def test_pending_payment_then_paid(self):
        folio, binding = self._new_folio()
        self._import(binding, btp="BTP|2026-07-03T23:02:48|Pending")
        self.assertFalse(self._payments(folio))
        self.assertEqual(
            binding.channel_payment_deadline, datetime.datetime(2026, 7, 3, 21, 2, 48)
        )
        notes = self._notes(folio, "online payment pending until")
        self.assertEqual(len(notes), 1)
        # Shown in the time of the hotel, not in UTC
        self.assertIn("23:02:48", notes[0])
        self.assertIn(self.agency.name, notes[0])
        self._import(binding, btp="BTP|2026-07-03T23:02:48|Pending")
        self.assertEqual(len(self._notes(folio, "online payment pending until")), 1)
        # The guest pays: the channel pushes a modification with the amount
        self._import(binding, paid=self._amount(218.03), due=self._amount(0))
        self.assertEqual(self._payments(folio).amount, 218.03)
        self.assertFalse(binding.channel_payment_deadline)
        self.assertEqual(len(self._notes(folio, "online payment pending until")), 1)

    def test_zero_zero_after_payment_keeps_everything(self):
        """A modification of a paid booking resets paid and due to 0."""
        folio, binding = self._new_folio()
        self._import(binding, paid=self._amount(150), due=self._amount(0))
        payment = self._payments(folio)
        messages = self._messages(folio)
        self._import(binding, paid=self._amount(0), due=self._amount(0))
        self.assertEqual(binding.channel_paid_amount, 150.0)
        self.assertEqual(self._payments(folio), payment)
        self.assertEqual(payment.amount, 150.0)
        self.assertEqual(binding.channel_paid_registered, 150.0)
        self.assertEqual(self._messages(folio), messages)

    def test_lower_amount_is_noted_once(self):
        folio, binding = self._new_folio()
        self._import(binding, paid=self._amount(150), due=self._amount(0))
        payment = self._payments(folio)
        for _attempt in range(2):
            self._import(binding, paid=self._amount(100), due=self._amount(50))
        self.assertEqual(self._payments(folio), payment)
        self.assertEqual(payment.amount, 150.0)
        self.assertEqual(binding.channel_paid_registered, 150.0)
        notes = self._notes(folio, "less than")
        self.assertEqual(len(notes), 1)
        self.assertIn("Refunds are not registered automatically", notes[0])

    def test_increase_registers_the_difference(self):
        folio, binding = self._new_folio()
        self._import(binding, paid=self._amount(100), due=self._amount(50))
        # A later payment by hand does not stop the channel sync once it
        # registered its first payment
        folio.do_payment(self.payment_method_line, self.env.user, 20.0, folio)
        self._import(binding, paid=self._amount(150), due=self._amount(0))
        self.assertEqual(
            sorted(binding.channel_payment_ids.mapped("amount")), [50.0, 100.0]
        )
        self.assertEqual(binding.channel_paid_registered, 150.0)
        self.assertEqual(len(self._payments(folio)), 3)

    def test_payment_cancelled_by_staff_is_not_registered_again(self):
        folio, binding = self._new_folio()
        self._import(binding, paid=self._amount(150), due=self._amount(0))
        payment = self._payments(folio)
        payment.action_draft()
        payment.action_cancel()
        self._import(binding, paid=self._amount(150), due=self._amount(0))
        self._import(binding, paid=self._amount(0), due=self._amount(0))
        self.assertFalse(self._payments(folio))
        self.assertEqual(binding.channel_payment_ids, payment)
        self.assertEqual(binding.channel_paid_registered, 150.0)

    def test_amount_over_the_folio_total(self):
        """Extras the folio does not hold yet: the real amount is registered."""
        folio, binding = self._new_folio()
        paid = folio.amount_total + 48.0
        self._import(binding, paid=self._amount(paid), due=self._amount(0))
        self.assertEqual(self._payments(folio).amount, paid)
        self.assertEqual(len(self._notes(folio, "exceeds the folio total")), 1)

    def test_modification_registers_only_the_difference(self):
        """The whole import of a booking the channel modifies once paid.

        Wubook keeps a modified booking as a new reservation code: the old
        code comes back with was_modified set (the importer discards it) and
        the new code names it in modified_reservations, so that its import
        updates the same binding. The total registered goes on from there.
        """
        old_code, new_code = 1780000101, 1780000102
        versions = {}
        fetched = self._run_import(
            versions, old_code, paid=self._amount(150), due=self._amount(50)
        )
        binding = self._binding(old_code)
        folio = binding.odoo_id
        self.assertEqual(folio.agency_id, self.agency)
        self.assertEqual(self._payments(folio).mapped("amount"), [150.0])
        self.assertEqual(binding.channel_paid_registered, 150.0)
        # The old code, modified, is discarded: the folio is not cancelled
        fetched += self._run_import(
            versions,
            old_code,
            paid=self._amount(150),
            due=self._amount(50),
            status=5,
            was_modified=1,
        )
        self.assertEqual(folio.state, "confirm")
        self.assertEqual(binding.wubook_status, "1")
        # The new code: the guest paid the rest online
        modified = {"modified_reservations": [old_code]}
        fetched += self._run_import(
            versions, new_code, paid=self._amount(200), due=self._amount(0), **modified
        )
        self.assertEqual(self._binding(new_code), binding)
        self.assertFalse(self._binding(old_code))
        self.assertEqual(binding.odoo_id, folio)
        self.assertEqual(sorted(self._payments(folio).mapped("amount")), [50.0, 150.0])
        self.assertEqual(binding.channel_paid_registered, 200.0)
        self.assertEqual(binding.channel_payment_ids, self._payments(folio))
        # Everything came with the imports: nothing was read from Wubook
        self.assertFalse(fetched)
        # Imported again (push, cron...): its dependencies fetch the old code
        # again, which is discarded, and nothing else is registered
        fetched = self._run_import(
            versions, new_code, paid=self._amount(200), due=self._amount(0), **modified
        )
        self.assertEqual(fetched, [old_code])
        self.assertEqual(sorted(self._payments(folio).mapped("amount")), [50.0, 150.0])
        self.assertEqual(binding.channel_paid_registered, 200.0)
        self.assertEqual(len(self._notes(folio, "registered automatically")), 2)
        self.assertEqual(
            self.env["channel.wubook.pms.folio"].search_count(
                [("backend_id", "=", self.backend.id)]
            ),
            1,
        )

    # cancellations

    def test_cancellation_keeps_the_payment(self):
        folio, binding = self._new_folio()
        self._import(binding, paid=self._amount(150), due=self._amount(0))
        payment = self._payments(folio)
        self._import(binding, paid=self._amount(150), due=self._amount(0), status=5)
        self.assertEqual(folio.state, "cancel")
        self.assertEqual(self._payments(folio), payment)
        self.assertEqual(payment.amount, 150.0)
        self.assertEqual(len(self._notes(folio, "Booking cancelled in")), 1)
        # Re-imports of the cancelled booking do not repeat the note
        self._import(binding, paid=self._amount(150), due=self._amount(0), status=5)
        self.assertEqual(len(self._notes(folio, "Booking cancelled in")), 1)
        # Reconfirmed and cancelled again: noted again
        self._import(binding, paid=self._amount(150), due=self._amount(0))
        self.assertEqual(folio.state, "confirm")
        self._import(binding, paid=self._amount(150), due=self._amount(0), status=5)
        self.assertEqual(len(self._notes(folio, "Booking cancelled in")), 2)
        self.assertEqual(self._payments(folio), payment)

    def test_booking_cancelled_from_the_first_version(self):
        folio, binding = self._new_folio()
        self._import(binding, paid=self._amount(150), due=self._amount(0), status=5)
        self.assertEqual(folio.state, "cancel")
        self.assertFalse(folio.payment_ids)
        self.assertEqual(binding.channel_paid_registered, 0.0)
        notes = self._notes(folio, "has not been registered automatically")
        self.assertEqual(len(notes), 1)
        self.assertIn("Booking cancelled in", notes[0])

    # what holds the registration

    def test_not_enabled_does_nothing(self):
        """Without a mapping flagged for the agency nothing visible changes."""
        self.ota_mapping.register_channel_payments = False
        folio, binding = self._new_folio()
        messages = self._messages(folio)
        self._import(binding, paid=self._amount(150), due=self._amount(0))
        # The information is kept on the binding only
        self.assertEqual(binding.channel_paid_amount, 150.0)
        self.assertFalse(folio.payment_ids)
        self.assertEqual(self._messages(folio), messages)
        self.assertFalse(binding.channel_payment_notes)

    def test_booking_without_payment_information_does_nothing(self):
        """Channels that do not report the paid amount are not affected."""
        folio, binding = self._new_folio()
        messages = self._messages(folio)
        booking = mirai_booking(paid=self._amount(150), due=self._amount(0))
        del booking["ancillary"]["Paid amount"]
        binding.write(self._mapper().channel_payment(booking))
        with mock.patch.object(
            type(binding), "_get_channel_payment_mapping"
        ) as get_mapping:
            self._importer()._after_import(binding)
        get_mapping.assert_not_called()
        self.assertFalse(binding.channel_payment_currency)
        self.assertFalse(folio.payment_ids)
        self.assertEqual(self._messages(folio), messages)

    def test_mapping_of_another_agency_does_nothing(self):
        other_agency = self.agency.copy({"name": "Other Engine"})
        self.ota_mapping.agency_id = other_agency
        folio, binding = self._new_folio()
        self._import(binding, paid=self._amount(150), due=self._amount(0))
        self.assertFalse(folio.payment_ids)

    def test_manual_payment_holds_the_registration(self):
        folio, binding = self._new_folio()
        folio.do_payment(self.payment_method_line, self.env.user, 150.0, folio)
        manual = self._payments(folio)
        for _attempt in range(2):
            self._import(binding, paid=self._amount(150), due=self._amount(0))
        self.assertEqual(self._payments(folio), manual)
        self.assertEqual(binding.channel_paid_registered, 0.0)
        self.assertEqual(len(self._notes(folio, "registered by hand")), 1)

    def test_cash_payment_holds_the_registration(self):
        folio, binding = self._new_folio()
        cash_journal = self.env["account.journal"].search(
            [("type", "=", "cash"), ("company_id", "=", self.company.id)], limit=1
        )
        self.env["account.bank.statement.line"].create(
            {
                "journal_id": cash_journal.id,
                "date": fields.Date.today(),
                "amount": 150.0,
                "payment_ref": "Cash",
                "folio_ids": [(6, 0, folio.ids)],
            }
        )
        self._import(binding, paid=self._amount(150), due=self._amount(0))
        self.assertFalse(self._payments(folio))
        self.assertEqual(len(self._notes(folio, "registered by hand")), 1)

    def test_other_currency_is_not_registered(self):
        folio, binding = self._new_folio()
        other = "EUR" if self.currency.name != "EUR" else "USD"
        for _attempt in range(2):
            self._import(binding, paid=f"150.00 {other}", due=f"0.00 {other}")
        self.assertFalse(folio.payment_ids)
        notes = self._notes(folio, f"but the folio is in {self.currency.name}")
        self.assertEqual(len(notes), 1)
        self.assertIn(f"150.00 {other}", notes[0])

    def test_payment_registered_on_another_folio_of_the_booking(self):
        """A duplicate folio of the same channel booking."""
        folio, binding = self._new_folio()
        self._import(binding, paid=self._amount(150), due=self._amount(0))
        duplicate, duplicate_binding = self._new_folio(external_id=1780000002)
        self._import(duplicate_binding, paid=self._amount(150), due=self._amount(0))
        self.assertFalse(duplicate.payment_ids)
        notes = self._notes(duplicate, "already registered on another folio")
        self.assertEqual(len(notes), 1)
        self.assertIn(folio.name, notes[0])
        # Another channel booking is registered as usual
        other, other_binding = self._new_folio(
            channel_code="TEST-0002", external_id=1780000003
        )
        self._import(other_binding, paid=self._amount(150), due=self._amount(0))
        self.assertEqual(self._payments(other).amount, 150.0)

    def test_split_booking_is_left_to_the_staff(self):
        folio, binding = self._new_folio()
        for _attempt in range(2):
            self._import(
                binding, paid=self._amount(150), due=self._amount(0), split=(1, 2)
            )
        self.assertEqual(binding.channel_payment_split, "1/2")
        self.assertFalse(folio.payment_ids)
        notes = self._notes(folio, "split in 2 folios")
        self.assertEqual(len(notes), 1)

    def test_split_guarantee_booking_is_silent(self):
        folio, binding = self._new_folio()
        messages = self._messages(folio)
        self._import(binding, paid=self._amount(0), due=self._amount(150), split=(2, 2))
        self.assertFalse(folio.payment_ids)
        self.assertEqual(self._messages(folio), messages)

    def test_card_guarantee_does_nothing(self):
        folio, binding = self._new_folio()
        messages = self._messages(folio)
        self._import(binding, paid=self._amount(0), due=self._amount(150))
        self.assertFalse(folio.payment_ids)
        self.assertEqual(self._messages(folio), messages)

    def test_gateway_fee_flow_is_unchanged(self):
        """Bookings with a prepayment (Booking.com, Expedia...) keep the
        payment_gateway_fee flow: one payment capped to the folio total."""
        folio, binding = self._new_folio()
        binding.payment_gateway_fee = folio.amount_total + 100
        self._import(binding, paid=self._amount(150), due=self._amount(0))
        payment = self._payments(folio)
        self.assertEqual(len(payment), 1)
        self.assertEqual(payment.amount, folio.amount_total)
        self.assertFalse(binding.channel_payment_ids)
        self.assertEqual(binding.channel_paid_registered, 0.0)
        self.assertFalse(binding.channel_payment_notes)

    # robustness

    def test_failure_does_not_fail_the_import(self):
        folio, binding = self._new_folio()
        importer = self._importer()
        with mock.patch.object(
            type(self.env["pms.folio"]),
            "do_payment",
            side_effect=UserError("No outstanding receipts account"),
        ), mock.patch.object(
            importer, "_refresh_availability_export"
        ) as refresh, mute_logger(IMPORTER_LOGGER):
            binding.write(
                self._mapper().channel_payment(
                    mirai_booking(paid=self._amount(150), due=self._amount(0))
                )
            )
            importer._after_import(binding)
        refresh.assert_called_once()
        self.assertFalse(folio.payment_ids)
        self.assertEqual(binding.channel_paid_registered, 0.0)
        notes = self._notes(folio, "could not be registered automatically")
        self.assertEqual(len(notes), 1)
        self.assertIn("No outstanding receipts account", notes[0])
        # The next import registers it
        self._import(binding, paid=self._amount(150), due=self._amount(0))
        self.assertEqual(self._payments(folio).amount, 150.0)
        self.assertNotIn("error", binding.channel_payment_notes or {})

    def test_failure_after_the_payment_rolls_it_back(self):
        """A failure once the payment is posted undoes the whole registration:
        a payment left without its registered total would be registered
        again by the next import."""
        folio_class = type(self.env["pms.folio"])
        binding_class = type(self.env["channel.wubook.pms.folio"])
        do_payment = folio_class.do_payment
        post_note = binding_class._post_channel_payment_note
        posted = []

        def fail_after_posting(folio, *args, **kwargs):
            do_payment(folio, *args, **kwargs)
            posted.append(self._posted_amounts(folio))
            raise ValueError("Failure after the payment")

        def fail_on_registered_note(binding, folio, kind, key, body):
            if kind == "registered":
                posted.append(self._posted_amounts(folio))
                raise ValueError("Failure after the payment")
            return post_note(binding, folio, kind, key, body)

        for number, (model_class, method, failure) in enumerate(
            (
                (folio_class, "do_payment", fail_after_posting),
                (binding_class, "_post_channel_payment_note", fail_on_registered_note),
            ),
            start=1,
        ):
            with self.subTest(method=method):
                folio, binding = self._new_folio(
                    channel_code=f"TEST-LATE-{number}",
                    external_id=1780000200 + number,
                )
                posted.clear()
                with mock.patch.object(model_class, method, failure), mute_logger(
                    IMPORTER_LOGGER
                ):
                    self._import(binding, paid=self._amount(150), due=self._amount(0))
                # It failed with the payment posted, and nothing is left
                self.assertEqual(posted, [[150.0]])
                self.assertFalse(self._posted_amounts(folio))
                self.assertFalse(
                    self.env["account.payment"].search([("folio_ids", "in", folio.ids)])
                )
                self.assertEqual(binding.channel_paid_registered, 0.0)
                self.assertFalse(binding.channel_payment_ids)
                self.assertFalse(self._notes(folio, "Payment:"))
                notes = self._notes(folio, "could not be registered automatically")
                self.assertEqual(len(notes), 1)
                self.assertIn("Failure after the payment", notes[0])
                # The next import registers it, once
                self._import(binding, paid=self._amount(150), due=self._amount(0))
                self._import(binding, paid=self._amount(150), due=self._amount(0))
                self.assertEqual(self._posted_amounts(folio), [150.0])
                self.assertEqual(binding.channel_paid_registered, 150.0)
                self.assertEqual(binding.channel_payment_ids, self._payments(folio))
                self.assertEqual(len(self._notes(folio, "Payment:")), 1)
                self.assertNotIn("error", binding.channel_payment_notes or {})

    def test_other_failures_still_fail_the_import(self):
        """Only the sync is fail-open. The work the import left pending (here
        a precommit hook) is flushed before it, so that its failures still
        make the import fail instead of being noted as a payment error."""
        folio, binding = self._new_folio()
        binding.write(
            self._mapper().channel_payment(
                mirai_booking(paid=self._amount(150), due=self._amount(0))
            )
        )
        importer = self._importer()
        hook = mock.Mock(side_effect=RuntimeError("Pending work failed"))
        with mock.patch.object(
            importer, "_refresh_availability_export"
        ), self.assertRaisesRegex(RuntimeError, "Pending work failed"):
            self.env.cr.precommit.add(hook)
            importer._after_import(binding)
        hook.assert_called_once_with()
        self.assertFalse(folio.payment_ids)
        self.assertEqual(binding.channel_paid_registered, 0.0)
        self.assertFalse(self._notes(folio, "could not be registered automatically"))

    def test_concurrency_errors_are_raised(self):
        """queue_job retries the import on these errors."""
        folio, binding = self._new_folio()
        for error in (
            psycopg2.errors.SerializationFailure("could not serialize access"),
            RetryableJobError("locked"),
        ):
            with self.subTest(error=error), mock.patch.object(
                type(self.env["pms.folio"]), "do_payment", side_effect=error
            ), self.assertRaises(type(error)):
                self._import(binding, paid=self._amount(150), due=self._amount(0))
        self.assertFalse(folio.payment_ids)

    def test_notes_in_the_language_of_the_property(self):
        """Not in the language of whoever triggered the import."""
        self.env["res.lang"]._activate_lang("es_ES")
        self.pms_property.lang = "es_ES"
        folio, binding = self._new_folio()
        self._import(binding, btp="BTP|2026-07-03T23:02:48|Pending")
        notes = self._notes(folio, "pago online pendiente hasta el")
        self.assertEqual(len(notes), 1)
        self.assertIn("03/07/2026 23:02:48", notes[0])
        self.assertIn("el canal cancelará la reserva", notes[0])
        self._import(binding, paid=self._amount(150), due=self._amount(0))
        notes = self._notes(folio, "registrado automáticamente")
        self.assertEqual(len(notes), 1)
        self.assertIn(f"{self.agency.name} comunica", notes[0])
        self.assertIn("150,00", notes[0])
        self._import(binding, paid=self._amount(100), due=self._amount(50))
        notes = self._notes(folio, "Las devoluciones no se registran")
        self.assertEqual(len(notes), 1)
        self.assertIn("100,00", notes[0])

    # configuration

    def test_mapping_needs_ota_and_incoming_method(self):
        other_agency = self.agency.copy({"name": "Other Engine"})
        outbound = self.bank_journal.outbound_payment_method_line_ids[:1]
        self.assertTrue(outbound)
        for values in (
            {"payment_method_line_id": False},
            {"agency_id": False},
            {"payment_method_line_id": outbound.id},
        ):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                self.ota_mapping.copy(dict({"agency_id": other_agency.id}, **values))
        # The same mapping is fine without the flag
        self.ota_mapping.copy(
            {
                "agency_id": other_agency.id,
                "payment_method_line_id": False,
                "register_channel_payments": False,
            }
        )

    def test_one_mapping_per_ota_registers(self):
        with self.assertRaises(ValidationError):
            self.ota_mapping.copy()
        # Several mappings per OTA are still allowed without the flag
        self.ota_mapping.copy({"register_channel_payments": False})
