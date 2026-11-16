import json
import re
from datetime import datetime, timedelta
from unittest import mock

import requests
from urllib3.exceptions import MaxRetryError, NewConnectionError, ProtocolError

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.pms.tests.common import TestPms

REQUESTS_POST = "odoo.addons.pms_api_rest.models.pms_neobookings_backend.requests.post"
DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

OK = (200, {"Response": {"StatusCode": 200, "RequestId": "x", "Success": {}}})
UNAUTHORIZED = (
    401,
    {"Response": {"StatusCode": 401, "Error": [{"Code": "401", "Description": "Bad"}]}},
)
SERVER_ERROR = (500, None)
UNAVAILABLE = (503, None)


def rejected(description="Order not found"):
    return (
        200,
        {
            "Response": {
                "StatusCode": 400,
                "Error": [{"Code": "404", "Description": description}],
            }
        },
    )


def connection_refused():
    return requests.ConnectionError(
        MaxRetryError(None, "/", NewConnectionError(None, "Connection refused"))
    )


def read_timeout():
    return requests.ReadTimeout("Read timed out")


def connection_dropped():
    return requests.ConnectionError(
        ProtocolError("Connection aborted.", ConnectionResetError())
    )


class FakeNeobookings:
    """Answers like the Neobookings API and records every request.

    Each request gets the next answer given, and OK when there are no more.
    An answer is a ``(status, json)`` tuple or an exception to raise.
    """

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        answer = self.answers.pop(0) if self.answers else OK
        if isinstance(answer, Exception):
            raise answer
        status, data = answer
        response = mock.Mock()
        response.status_code = status
        if data is None:
            response.json.side_effect = ValueError("No JSON")
            response.text = "Internal Server Error"
        else:
            response.json.return_value = data
            response.text = json.dumps(data)
        return response

    @property
    def payment_calls(self):
        return [call["json"] for call in self.calls]

    @property
    def quantities(self):
        return [body["Payment"]["Quantity"] for body in self.payment_calls]


@tagged("post_install", "-at_install")
class TestNeobookingsPayment(TestPms, AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(user=cls.env["res.users"].browse(1))
        cls.company = cls.company_data_2["company"]
        cls.bank_journal = cls.company_data_2["default_journal_bank"]
        cls.other_bank_journal = cls.bank_journal.copy(
            {"name": "Neopayments", "code": "NEOP"}
        )
        invoice_journal = cls.env["account.journal"].create(
            {
                "name": "Test Invoice Journal",
                "code": "TNEO",
                "type": "sale",
                "company_id": cls.company.id,
            }
        )
        cls.property = cls.env["pms.property"].create(
            {
                "name": "Neobookings Property",
                "company_id": cls.company.id,
                "journal_simplified_invoice_id": invoice_journal.id,
                "default_pricelist_id": cls.pricelist1.id,
            }
        )
        cls.other_property = cls.env["pms.property"].create(
            {
                "name": "Property without Neobookings",
                "company_id": cls.company.id,
                "journal_simplified_invoice_id": invoice_journal.id,
                "default_pricelist_id": cls.pricelist1.id,
            }
        )
        cls.sale_channel = cls.env["pms.sale.channel"].create(
            {"name": "Door", "channel_type": "direct"}
        )
        cls.room_type = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [(6, 0, (cls.property | cls.other_property).ids)],
                "name": "Double Neo",
                "default_code": "DBL_NEO",
                "class_id": cls.room_type_class1.id,
                "list_price": 100,
            }
        )
        # Several folios share the same dates in one property
        for pms_property in cls.property | cls.other_property:
            for number in range(5):
                cls.env["pms.room"].create(
                    {
                        "pms_property_id": pms_property.id,
                        "name": f"Room {pms_property.id}-{number}",
                        "room_type_id": cls.room_type.id,
                        "capacity": 2,
                    }
                )
        cls.partner = cls.env["res.partner"].create({"name": "Guest"})
        cls.neo_user = cls.env["res.users"].create(
            {
                "name": "Neobookings API",
                "login": "neobookings_api_test",
                "external_public_token": "neo-token",
            }
        )
        cls.backend = cls.env["pms.neobookings.backend"].create(
            {
                "name": "Neobookings",
                "dry_run": False,
                "url": "https://neo.test/api/v2/",
                "api_user_id": cls.neo_user.id,
                "pms_property_ids": [(6, 0, cls.property.ids)],
                "excluded_journal_ids": [(6, 0, cls.other_bank_journal.ids)],
            }
        )

    # Helpers

    def _set_create_uid(self, records, user):
        """Make ``user`` the creator of ``records`` (a recordset or a list)."""
        self.env.flush_all()
        for record_set in records if isinstance(records, list) else [records]:
            self.env.cr.execute(
                f"UPDATE {record_set._table} SET create_uid = %s WHERE id IN %s",
                (user.id, tuple(record_set.ids)),
            )
            record_set.invalidate_recordset(["create_uid"])

    def _create_folio(self, pms_property=None, external_reference="NEO-1", neo=True):
        reservation = self.env["pms.reservation"].create(
            {
                "checkin": datetime.now(),
                "checkout": datetime.now() + timedelta(days=1),
                "adults": 2,
                "pms_property_id": (pms_property or self.property).id,
                "room_type_id": self.room_type.id,
                "partner_id": self.partner.id,
                "sale_channel_origin_id": self.sale_channel.id,
            }
        )
        folio = reservation.folio_id
        folio.external_reference = external_reference
        if neo:
            self._set_create_uid([folio, reservation], self.neo_user)
        return folio

    def _create_payment(
        self, folios, amount=50.0, journal=None, payment_type="inbound", user=None
    ):
        journal = journal or self.bank_journal
        method_lines = (
            journal.inbound_payment_method_line_ids
            if payment_type == "inbound"
            else journal.outbound_payment_method_line_ids
        )
        payment = self.env["account.payment"].create(
            {
                "payment_type": payment_type,
                "partner_type": "customer",
                "journal_id": journal.id,
                "payment_method_line_id": method_lines[:1].id,
                "amount": amount,
                "partner_id": self.partner.id,
                "folio_ids": [(6, 0, folios.ids)],
            }
        )
        if user:
            self._set_create_uid(payment, user)
        payment.action_post()
        return payment

    def _sync(self, *answers):
        """Like the cron, without waiting for the next attempt dates."""
        fake = FakeNeobookings(*answers)
        with mock.patch(REQUESTS_POST, side_effect=fake):
            self.backend._mark_changed_folios()
            self.backend._sync_pending(force=True)
        return fake

    def _neo_amount(self, folio):
        return self.backend._get_neobookings_amount(folio)

    def _get_sync(self, payment):
        return self.env["pms.neobookings.payment.sync"].search(
            [("payment_id", "=", payment.id)]
        )

    def _change_amount(self, payment, amount):
        payment.action_draft()
        payment.amount = amount
        payment.action_post()

    # Sending

    def test_reception_payment_is_sent(self):
        folio = self._create_folio()
        payment = self._create_payment(folio, amount=50.0)
        sync = self._get_sync(payment)
        self.assertEqual(sync.state, "pending")
        self.assertEqual(sync.folio_id, folio)

        fake = self._sync()

        self.assertEqual(len(fake.calls), 1)
        call = fake.calls[0]
        self.assertEqual(call["url"], "https://neo.test/api/v2/OrderPaymentCreateRQ")
        self.assertEqual(call["headers"]["Authorization"], "Bearer neo-token")
        self.assertEqual(call["timeout"], (10, 30))
        body = call["json"]
        self.assertEqual(body["OrderId"], "NEO-1")
        self.assertEqual(body["OrderIdOrigin"], "NEO-1")
        self.assertEqual(body["Request"]["Language"], "es")
        self.assertNotIn("TokenTpv", body)
        self.assertEqual(
            body["Payment"],
            {
                "DateCreated": body["Payment"]["DateCreated"],
                "Method": "other",
                "Quantity": 50.0,
                "Currency": payment.currency_id.name,
                "Description": payment.name,
                "Removed": False,
            },
        )
        self.assertTrue(
            body["Payment"]["DateCreated"].startswith(str(payment.date) + " ")
        )
        self.assertEqual(sync.state, "done")
        self.assertFalse(sync.inflight_data)
        self.assertEqual(json.loads(sync.sent_data)["order"], "NEO-1")
        self.assertEqual(json.loads(sync.last_request), body)

    def test_payment_method(self):
        cash = self._create_payment(
            self._create_folio(), journal=self.company_data_2["default_journal_cash"]
        )
        bank = self._create_payment(self._create_folio(external_reference="NEO-2"))
        online = self._create_payment(self._create_folio(external_reference="NEO-3"))
        provider = self.env["payment.provider"].create(
            {"name": "Test provider", "code": "none", "company_id": self.company.id}
        )
        online.payment_transaction_id = self.env["payment.transaction"].create(
            {
                "provider_id": provider.id,
                "reference": "NEO-TX-1",
                "amount": online.amount,
                "currency_id": online.currency_id.id,
                "partner_id": self.partner.id,
            }
        )

        fake = self._sync()

        methods = {
            body["Payment"]["Description"]: body["Payment"]["Method"]
            for body in fake.payment_calls
        }
        self.assertEqual(
            methods, {cash.name: "cash", bank.name: "other", online.name: "tpv"}
        )

    def test_payments_not_reported(self):
        neo_folio = self._create_folio()
        cases = {
            "created by Neobookings": self._create_payment(
                neo_folio, user=self.neo_user
            ),
            "excluded journal": self._create_payment(
                neo_folio, journal=self.other_bank_journal
            ),
            "folio not from Neobookings": self._create_payment(
                self._create_folio(neo=False)
            ),
            "folio without reference": self._create_payment(
                self._create_folio(external_reference=False)
            ),
            "property not enabled": self._create_payment(
                self._create_folio(pms_property=self.other_property)
            ),
            "several folios": self._create_payment(
                neo_folio | self._create_folio(external_reference="NEO-2")
            ),
        }
        for case, payment in cases.items():
            with self.subTest(case=case):
                self.assertFalse(self._get_sync(payment))
        fake = self._sync()
        self.assertFalse(fake.calls)

    # Changes: Neobookings adds every request up, so they are reversed

    def test_draft_after_send_reverses_payment(self):
        payment = self._create_payment(self._create_folio())
        self._sync()
        sent = json.loads(self._get_sync(payment).sent_data)

        payment.action_draft()
        sync = self._get_sync(payment)
        self.assertEqual(sync.state, "pending")
        fake = self._sync()

        self.assertEqual(len(fake.payment_calls), 1)
        body = fake.payment_calls[0]
        self.assertEqual(body["OrderId"], "NEO-1")
        reversal = body["Payment"]
        self.assertEqual(reversal["Quantity"], -50.0)
        self.assertEqual(reversal["Description"], payment.name + " (reversal)")
        self.assertEqual(reversal["Method"], sent["payment"]["Method"])
        self.assertEqual(reversal["Currency"], sent["payment"]["Currency"])
        self.assertRegex(reversal["DateCreated"], DATETIME_RE)
        self.assertFalse(reversal["Removed"])
        self.assertFalse(sync.sent_data)
        self.assertEqual(sync.state, "done")

        payment.action_cancel()
        fake = self._sync()
        self.assertFalse(fake.calls)

    def test_repost_unchanged_sends_nothing(self):
        payment = self._create_payment(self._create_folio())
        self._sync()
        payment.action_draft()
        payment.action_post()
        fake = self._sync()
        self.assertFalse(fake.calls)
        self.assertEqual(self._get_sync(payment).state, "done")

    def test_amount_change_reverses_and_sends_again(self):
        payment = self._create_payment(self._create_folio(), amount=50.0)
        self._sync()
        self._change_amount(payment, 80.0)

        fake = self._sync()

        self.assertEqual(fake.quantities, [-50.0, 80.0])
        self.assertEqual(
            [body["Payment"]["Description"] for body in fake.payment_calls],
            [payment.name + " (reversal)", payment.name],
        )
        self.assertFalse(any(body["Payment"]["Removed"] for body in fake.payment_calls))
        sync = self._get_sync(payment)
        self.assertEqual(sync.state, "done")
        self.assertEqual(json.loads(sync.sent_data)["payment"]["Quantity"], 80.0)

    def test_failure_after_reversal_does_not_repeat_it(self):
        payment = self._create_payment(self._create_folio(), amount=50.0)
        self._sync()
        self._change_amount(payment, 80.0)

        fake = self._sync(OK, rejected())

        self.assertEqual(fake.quantities, [-50.0, 80.0])
        sync = self._get_sync(payment)
        self.assertFalse(sync.sent_data, "The reversal was received")
        self.assertEqual(sync.state, "pending")
        self.assertEqual(sync.attempts, 1)

        fake = self._sync()
        self.assertEqual(fake.quantities, [80.0])
        self.assertEqual(sync.state, "done")

    def test_draft_before_first_send_sends_nothing(self):
        payment = self._create_payment(self._create_folio())
        payment.action_draft()
        fake = self._sync()
        self.assertFalse(fake.calls)
        self.assertEqual(self._get_sync(payment).state, "done")

    def test_unlink_after_send_reverses_payment(self):
        payment = self._create_payment(self._create_folio())
        self._sync()
        sync = self._get_sync(payment)
        payment.action_draft()
        payment.unlink()
        self.assertFalse(sync.payment_id)
        self.assertEqual(sync.state, "pending")

        fake = self._sync()

        self.assertEqual(fake.quantities, [-50.0])
        self.assertEqual(sync.state, "done")
        self.assertFalse(sync.sent_data)

    def test_refund_is_negative_and_its_reversal_positive(self):
        folio = self._create_folio()
        self._create_payment(folio, amount=50.0)
        self._sync()
        refund = self._create_payment(folio, amount=20.0, payment_type="outbound")
        fake = self._sync()
        self.assertEqual(fake.quantities, [-20.0])
        refund.action_draft()
        fake = self._sync()
        self.assertEqual(fake.quantities, [20.0])

    # Never more than the amount of the Neobookings reservation

    def test_payment_capped_to_reservation_amount(self):
        folio = self._create_folio()
        amount = self._neo_amount(folio)
        self.assertGreaterEqual(amount, 100.0, "The other tests rely on it")
        payment = self._create_payment(folio, amount=amount + 30)

        fake = self._sync()

        self.assertEqual(fake.quantities, [amount])
        self.assertEqual(fake.payment_calls[0]["Payment"]["Description"], payment.name)
        sent = json.loads(self._get_sync(payment).sent_data)
        self.assertEqual(sent["payment"]["Quantity"], amount)

    def test_extra_after_full_payment_not_reported(self):
        folio = self._create_folio()
        amount = self._neo_amount(folio)
        self._create_payment(folio, amount=amount)
        extra = self._create_payment(folio, amount=30.0)

        fake = self._sync()

        self.assertEqual(fake.quantities, [amount])
        sync = self._get_sync(extra)
        self.assertEqual(sync.state, "done")
        self.assertFalse(sync.sent_data)

    def test_reservation_added_in_pms_is_left_out(self):
        folio = self._create_folio()
        amount = self._neo_amount(folio)
        self.env["pms.reservation"].create(
            {
                "folio_id": folio.id,
                "checkin": datetime.now(),
                "checkout": datetime.now() + timedelta(days=1),
                "adults": 2,
                "pms_property_id": self.property.id,
                "room_type_id": self.room_type.id,
                "partner_id": self.partner.id,
                "sale_channel_origin_id": self.sale_channel.id,
            }
        )
        self.assertGreater(folio.amount_total, amount)
        self.assertEqual(self._neo_amount(folio), amount)
        self._create_payment(folio, amount=amount + 50)

        self.assertEqual(self._sync().quantities, [amount])

    def test_neobookings_charge_reduces_what_is_reported(self):
        folio = self._create_folio()
        amount = self._neo_amount(folio)
        neo_charge = self._create_payment(folio, amount=40.0, user=self.neo_user)
        self.assertFalse(self._get_sync(neo_charge))
        self._create_payment(folio, amount=amount)

        self.assertEqual(self._sync().quantities, [round(amount - 40, 2)])

        # Without that charge the whole payment fits
        neo_charge.action_draft()
        fake = self._sync()
        self.assertEqual(fake.quantities, [-round(amount - 40, 2), amount])

    def test_excluded_journal_counts_as_paid_by_neobookings(self):
        folio = self._create_folio()
        amount = self._neo_amount(folio)
        self._create_payment(folio, amount=40.0, journal=self.other_bank_journal)
        self._create_payment(folio, amount=amount)

        self.assertEqual(self._sync().quantities, [round(amount - 40, 2)])

    def test_refund_of_neobookings_charge_is_reported(self):
        folio = self._create_folio()
        amount = self._neo_amount(folio)
        self._create_payment(folio, amount=amount, user=self.neo_user)
        self._create_payment(folio, amount=amount, payment_type="outbound")

        self.assertEqual(self._sync().quantities, [-amount])

    def test_refund_without_payments_is_not_reported(self):
        refund = self._create_payment(
            self._create_folio(), amount=20.0, payment_type="outbound"
        )
        self.assertFalse(self._sync().calls)
        self.assertEqual(self._get_sync(refund).state, "done")

    def test_cancelled_payment_frees_room_for_the_next(self):
        folio = self._create_folio()
        amount = self._neo_amount(folio)
        first = self._create_payment(folio, amount=amount)
        second = self._create_payment(folio, amount=30.0)
        self.assertEqual(self._sync().quantities, [amount])

        first.action_draft()
        fake = self._sync()

        self.assertEqual(fake.quantities, [-amount, 30.0])
        self.assertFalse(self._get_sync(first).sent_data)
        sent = json.loads(self._get_sync(second).sent_data)
        self.assertEqual(sent["payment"]["Quantity"], 30.0)

    def test_reservation_amount_change_is_followed(self):
        folio = self._create_folio()
        amount = self._neo_amount(folio)
        self._create_payment(folio, amount=amount + 20)
        self.assertEqual(self._sync().quantities, [amount])

        line = folio.reservation_ids.reservation_line_ids[:1]
        line.price += 10
        new_amount = self._neo_amount(folio)
        self.assertGreater(new_amount, amount)
        fake = self._sync()

        self.assertEqual(fake.quantities, [-amount, new_amount])

    def test_unchanged_folio_sends_nothing_again(self):
        self._create_payment(self._create_folio(), amount=50.0)
        self._sync()
        self.assertFalse(self._sync().calls)

    # Failures that surely did not reach Neobookings are retried

    def test_rejected_payment_is_retried_then_left_in_error(self):
        self.backend.max_attempts = 2
        payment = self._create_payment(self._create_folio())

        self._sync(rejected("Order not found"))

        sync = self._get_sync(payment)
        self.assertEqual(sync.state, "pending")
        self.assertEqual(sync.attempts, 1)
        self.assertIn("Order not found", sync.last_error)
        self.assertGreater(sync.next_attempt_date, fields.Datetime.now())
        self.assertFalse(sync.sent_data)
        self.assertFalse(sync.inflight_data)

        self._sync(rejected())
        self.assertEqual(sync.state, "error")
        self.assertEqual(sync.attempts, 2)

        sync.action_retry()
        self.assertEqual(sync.state, "pending")
        self.assertEqual(sync.attempts, 0)
        self._sync()
        self.assertEqual(sync.state, "done")
        self.assertTrue(sync.sent_data)

    def test_failures_before_reaching_neobookings_are_retried(self):
        answers = {
            "unauthorized": UNAUTHORIZED,
            "unavailable": UNAVAILABLE,
            "connection refused": connection_refused(),
        }
        for case, answer in answers.items():
            with self.subTest(case=case):
                payment = self._create_payment(self._create_folio())
                self._sync(answer)
                sync = self._get_sync(payment)
                self.assertEqual(sync.state, "pending")
                self.assertEqual(sync.attempts, 1)
                self.assertFalse(sync.inflight_data)
                self.assertGreater(sync.next_attempt_date, fields.Datetime.now())
                self._sync()
                self.assertEqual(sync.state, "done")

    def test_missing_token_is_retried(self):
        payment = self._create_payment(self._create_folio())
        self.neo_user.external_public_token = False

        fake = self._sync()

        self.assertFalse(fake.calls)
        sync = self._get_sync(payment)
        self.assertEqual(sync.state, "pending")
        self.assertEqual(sync.attempts, 1)
        self.assertIn("token", sync.last_error)

    def test_cron_waits_for_next_attempt(self):
        payment = self._create_payment(self._create_folio())
        self._sync(UNAVAILABLE)
        fake = FakeNeobookings()
        with mock.patch(REQUESTS_POST, side_effect=fake):
            self.env["pms.neobookings.backend"]._cron_sync_payments()
        self.assertFalse(fake.calls)
        self.assertEqual(self._get_sync(payment).state, "pending")

    def test_sync_now_sends_waiting_payments(self):
        payment = self._create_payment(self._create_folio())
        self._sync(UNAVAILABLE)
        sync = self._get_sync(payment)
        cron = self.env.ref("pms_api_rest.ir_cron_neobookings_sync_payments")
        triggers = self.env["ir.cron.trigger"].search([("cron_id", "=", cron.id)])

        self.backend.action_sync_now()

        self.assertLessEqual(sync.next_attempt_date, fields.Datetime.now())
        self.assertGreater(
            self.env["ir.cron.trigger"].search_count([("cron_id", "=", cron.id)]),
            len(triggers),
        )

    # Failures that may have reached Neobookings wait for a person

    def test_no_answer_waits_for_review(self):
        answers = {
            "read timeout": read_timeout(),
            "connection dropped": connection_dropped(),
            "server error": SERVER_ERROR,
        }
        for case, answer in answers.items():
            with self.subTest(case=case):
                payment = self._create_payment(self._create_folio())
                self._sync(answer)
                sync = self._get_sync(payment)
                self.assertEqual(sync.state, "review")
                self.assertTrue(sync.last_error)
                self.assertFalse(sync.sent_data)
                inflight = json.loads(sync.inflight_data)
                self.assertEqual(inflight["request"]["Payment"]["Quantity"], 50.0)
                self.assertEqual(inflight["sent_data"]["payment"]["Quantity"], 50.0)

                # Neither a new run nor a change of the payment sends it again
                self.assertFalse(self._sync().calls)
                payment.action_draft()
                self.assertFalse(self._sync().calls)
                self.assertEqual(sync.state, "review")

    def test_review_marked_received(self):
        payment = self._create_payment(self._create_folio())
        self._sync(read_timeout())
        sync = self._get_sync(payment)

        sync.action_mark_received()

        self.assertEqual(sync.state, "pending")
        self.assertFalse(sync.inflight_data)
        self.assertEqual(json.loads(sync.sent_data)["payment"]["Quantity"], 50.0)
        fake = self._sync()
        self.assertFalse(fake.calls)
        self.assertEqual(sync.state, "done")

    def test_review_marked_received_after_cancellation(self):
        payment = self._create_payment(self._create_folio())
        self._sync(read_timeout())
        payment.action_draft()
        sync = self._get_sync(payment)

        sync.action_mark_received()
        fake = self._sync()

        self.assertEqual(fake.quantities, [-50.0])
        self.assertEqual(sync.state, "done")
        self.assertFalse(sync.sent_data)

    def test_review_marked_not_received(self):
        payment = self._create_payment(self._create_folio())
        self._sync(SERVER_ERROR)
        sync = self._get_sync(payment)

        sync.action_mark_not_received()

        self.assertEqual(sync.state, "pending")
        self.assertFalse(sync.inflight_data)
        self.assertFalse(sync.sent_data)
        fake = self._sync()
        self.assertEqual(fake.quantities, [50.0])
        self.assertEqual(sync.state, "done")

    def test_interrupted_request_goes_to_review(self):
        payment_1 = self._create_payment(self._create_folio())
        payment_2 = self._create_payment(self._create_folio(external_reference="NEO-2"))
        inflight = json.dumps({"request": {}, "sent_data": None})
        sync_1 = self._get_sync(payment_1)
        sync_2 = self._get_sync(payment_2)
        # The process died while sending; the second payment changed after
        sync_1.write({"state": "sending", "inflight_data": inflight})
        sync_2.write({"state": "pending", "inflight_data": inflight})

        fake = FakeNeobookings()
        with mock.patch(REQUESTS_POST, side_effect=fake):
            self.env["pms.neobookings.backend"]._cron_sync_payments()

        self.assertFalse(fake.calls)
        self.assertEqual((sync_1 | sync_2).mapped("state"), ["review", "review"])

    def test_tracking_failure_never_blocks_the_payment(self):
        sync_model = self.registry["pms.neobookings.payment.sync"]
        with mock.patch.object(
            sync_model, "_track_payments", side_effect=ValueError("boom")
        ), self.assertLogs(
            "odoo.addons.pms_api_rest.models.pms_neobookings_payment_sync",
            level="ERROR",
        ):
            payment = self._create_payment(self._create_folio())
        self.assertEqual(payment.state, "posted")
        self.assertFalse(self._get_sync(payment))

    # Configuration

    def test_property_in_one_connection_only(self):
        with self.assertRaises(ValidationError):
            self.backend.copy(
                {"name": "Neobookings 2"}
            ).pms_property_ids = self.property

    def test_simulation_stores_requests_without_sending(self):
        self.backend.dry_run = True
        payment = self._create_payment(self._create_folio(), amount=50.0)

        fake = self._sync()

        self.assertFalse(fake.calls)
        sync = self._get_sync(payment)
        self.assertEqual(sync.state, "simulated")
        self.assertFalse(sync.sent_data)
        requests_ = json.loads(sync.last_request)
        self.assertEqual(len(requests_), 1)
        self.assertEqual(requests_[0]["OrderId"], "NEO-1")
        self.assertEqual(requests_[0]["Payment"]["Quantity"], 50.0)
        self.assertFalse(requests_[0]["Payment"]["Removed"])

        # A change after the simulation is simulated again
        payment.action_draft()
        self.assertEqual(sync.state, "pending")
        fake = self._sync()
        self.assertFalse(fake.calls)
        self.assertEqual(sync.state, "done", "Nothing was sent, nothing to reverse")

    def test_simulation_needs_no_token(self):
        user = self.env["res.users"].create(
            {"name": "Neobookings without token", "login": "neobookings_no_token"}
        )
        backend = self.env["pms.neobookings.backend"].create(
            {"name": "Neobookings simulation", "api_user_id": user.id}
        )
        self.assertTrue(backend.dry_run)
        with self.assertRaises(ValidationError):
            backend.dry_run = False
