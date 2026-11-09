# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""Cards Wubook keeps: recorded on import, read on demand, never kept."""

import datetime
import logging
from unittest.mock import patch

from odoo.tests import tagged

from odoo.addons.connector_pms_wubook.tests.common import TestWubookConnector
from odoo.addons.pms_payment_card.exceptions import (
    CardNotAvailable,
    ChannelManagerUnavailable,
)
from odoo.addons.pms_payment_card.tests.common import PASSWORD, PaymentCardCase

from ..models.pms_folio_payment_card import parse_wubook_card

WUBOOK_PASSWORD = "wubook-card-secret"
PAN = "4242424242424242"


def _timestamp(day):
    moment = datetime.datetime.combine(day, datetime.time(12))
    return int(moment.replace(tzinfo=datetime.timezone.utc).timestamp())


class _FakeWubook:
    """Stands in for ``xmlrpc.client.ServerProxy``."""

    def __init__(self, answer=None, error=None):
        self.answer = answer
        self.error = error
        self.calls = []

    def __call__(self, url, transport=None):
        return self

    def fetch_ccard(self, *args):
        self.calls.append(args)
        if self.error:
            raise self.error
        return self.answer


class _CollectLogs(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


@tagged("post_install", "-at_install")
class TestWubookPaymentCard(PaymentCardCase, TestWubookConnector):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        payment_method_line = cls.env["account.payment.method.line"].search([], limit=1)
        cls.backend = cls.env["channel.wubook.backend"].create(
            {
                "name": "Wubook test backend",
                "pms_property_id": cls.pms_property1.id,
                "user_id": cls.env.ref("base.user_root").id,
                "backend_type_id": cls.backend_type1.parent_id.id,
                "pricelist_external_id": 1,
                "wubook_payment_method_line_id": payment_method_line.id,
                "permanent_token": "permanent-token",
                **cls.fake_credentials,
            }
        )
        cls.binding = (
            cls.env["channel.wubook.pms.folio"]
            .with_context(connector_no_export=True)
            .create(
                {
                    "odoo_id": cls.folio.id,
                    "backend_id": cls.backend.id,
                    "external_id": "1700000001",
                }
            )
        )

    def _import(self, **booking):
        values = {
            "reservation_code": 1700000001,
            "cc_info": 1,
            "id_channel": 2,
            "channel_reservation_code": "4093826715",
            "date_departure": datetime.date(2026, 12, 2),
            "channel_data": {},
        }
        values.update(booking)
        with self.backend.work_on("channel.wubook.pms.folio") as work:
            importer = work.component(usage="direct.record.importer")
            importer._payment_card_booking = values
            importer._sync_payment_card(self.binding)
        return self.env["pms.folio.payment.card"].search(
            [("folio_id", "=", self.folio.id), ("source_type", "=", "channel")]
        )

    def _enable(self):
        self.env["channel.wubook.backend.card.password.wizard"].create(
            {
                "backend_id": self.backend.id,
                "password": WUBOOK_PASSWORD,
                "confirm_password": WUBOOK_PASSWORD,
            }
        )
        self.backend.payment_card_fetch_enabled = True

    def _details(self, fake):
        with patch("xmlrpc.client.ServerProxy", fake):
            return self.cards()._api_details(self.folio.id, PASSWORD)

    def _fetch_logs(self):
        return self.env["channel.backend.log"].search(
            [("backend_id", "=", self.backend.parent_id.id)]
        )

    # Import ----------------------------------------------------------------

    def test_import_records_the_card_and_the_virtual_card(self):
        card = self._import(
            channel_data={
                "vcc_additional_info": {
                    "vcc_valid_from": _timestamp(datetime.date(2026, 11, 10)),
                    "vcc_valid_til": _timestamp(datetime.date(2026, 12, 10)),
                    "vcc_balance": 120.5,
                    "vcc_currency_code": "EUR",
                }
            }
        )

        self.assertEqual(card.provider, "wubook")
        self.assertEqual(card.wubook_backend_id, self.backend)
        self.assertEqual(card.channel_code, "1700000001")
        self.assertEqual(card.external_reference, "4093826715")
        self.assertEqual(card.available_until, datetime.date(2026, 12, 9))
        self.assertFalse(card.payload)
        summary = self.cards()._api_summary(self.folio.id)
        self.assertEqual(
            summary["virtualCard"],
            {
                "chargeableFrom": "2026-11-10",
                "chargeableUntil": "2026-12-10",
                "balance": {"amount": 120.5, "currency": "EUR"},
            },
        )

    def test_a_booking_without_card_records_nothing(self):
        self.assertFalse(self._import(cc_info=0, channel_data=[]))

    # Reading the card ------------------------------------------------------

    def test_the_card_is_read_from_wubook_and_leaves_no_trace(self):
        self._import()
        self._enable()
        fake = _FakeWubook(
            answer=[
                0,
                {
                    "cc_owner": "Jane Doe",
                    "cc_number": PAN,
                    "cc_expiring": "03/2030",
                    "cc_cvv": "123",
                    "cc_type": 1,
                    "cc_code": "",
                },
            ]
        )
        collector = _CollectLogs()
        root = logging.getLogger()
        level = root.level
        root.addHandler(collector)
        root.setLevel(logging.DEBUG)
        try:
            details = self._details(fake)
        finally:
            root.removeHandler(collector)
            root.setLevel(level)

        self.assertEqual(
            fake.calls, [("permanent-token", "X", "1700000001", WUBOOK_PASSWORD)]
        )
        self.assertEqual(details["pan"], PAN)
        self.assertEqual((details["expiryMonth"], details["expiryYear"]), (3, 2030))
        self.assertEqual(details["cvv"], "123")
        logs = self._fetch_logs()
        self.assertEqual(logs.mapped("arguments"), ["[redacted]"])
        self.assertEqual(logs.mapped("response"), ["[redacted]"])
        for message in collector.messages:
            self.assertNotIn(WUBOOK_PASSWORD, message)
            self.assertNotIn(PAN, message)
        self.assertNotIn(WUBOOK_PASSWORD, self.backend.sudo().payment_card_password)

    def test_wubook_no_longer_has_the_card(self):
        card = self._import()
        self._enable()
        fake = _FakeWubook(answer=[-17, "No CC for this reservation"])

        self.refused(CardNotAvailable, lambda: self._details(fake))

        self.assertEqual(card.state, "not_available")

    def test_wubook_does_not_answer(self):
        self._import()
        self._enable()
        fake = _FakeWubook(error=OSError("timed out"))

        self.refused(ChannelManagerUnavailable, lambda: self._details(fake))

        self.assertEqual(self._fetch_logs().mapped("response_code"), ["error"])

    def test_nothing_is_asked_when_reading_is_off(self):
        self._import()
        fake = _FakeWubook(answer=[0, {}])

        self.refused(ChannelManagerUnavailable, lambda: self._details(fake))
        self._enable()
        self.backend.payment_card_fetch_enabled = False
        self.refused(ChannelManagerUnavailable, lambda: self._details(fake))

        self.assertFalse(fake.calls)

    def test_forty_requests_an_hour(self):
        self._import()
        self._enable()
        fake = _FakeWubook(answer=[-17, "No CC for this reservation"])
        method = self.backend._payment_card_fetch_method()
        for _call in range(40):
            self.backend._payment_card_log_call(method, 0)

        self.refused(ChannelManagerUnavailable, lambda: self._details(fake))

        self.assertFalse(fake.calls)

    # Passwords -------------------------------------------------------------

    def test_resetting_the_property_password_drops_the_wubook_one(self):
        self._enable()
        self.assertTrue(self.backend.payment_card_password_configured)

        self.vault._reset_password("a brand new password")

        self.assertFalse(self.backend.sudo().payment_card_password)

    def test_parse_wubook_card(self):
        self.assertEqual(
            parse_wubook_card({"cc_expiring": "3/30", "cc_number": "4242 4242"}),
            {
                "holder": "",
                "pan": "42424242",
                "expiry_month": 3,
                "expiry_year": 2030,
                "cvv": "",
            },
        )
        self.assertEqual(parse_wubook_card(None)["expiry_month"], 0)
