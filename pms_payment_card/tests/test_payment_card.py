# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""The payment card of a folio, as the API serves it.

These tests go through the ``_api_*`` methods the REST service calls, as the
user that calls it, and check the answers the front relies on.
"""

import base64
import datetime
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from ..exceptions import (
    CardAlreadyExists,
    CardForbidden,
    CardNotAvailable,
    CardPasswordLocked,
    CardPasswordNotConfigured,
    CardValidationError,
    ChannelManagerUnavailable,
    FolioNotFound,
    InvalidCardPassword,
)
from .common import PASSWORD, VISA, PaymentCardCase

CHANNEL_CARD = {
    "holder": "John Smith",
    "pan": "5555555555554444",
    "expiry_month": 12,
    "expiry_year": 2030,
    "cvv": "",
}


@tagged("post_install", "-at_install")
class TestPaymentCard(PaymentCardCase):
    def _patch_fetch(self, error=None):
        """Stand in for the bridge of a channel manager called ``fake``."""
        calls = []

        def fetch(card, private_key):
            calls.append(card)
            if error:
                raise error
            return dict(CHANNEL_CARD)

        model_class = type(self.env["pms.folio.payment.card"])
        patcher = patch.object(
            model_class, "_fetch_channel_card_fake", fetch, create=True
        )
        return patcher, calls

    def _lock_now(self, now):
        model_class = type(self.env["pms.payment.card.lock"])
        return patch.object(model_class, "_now", return_value=now)

    def _details(self, password, user=None):
        return self.cards(user)._api_details(self.folio.id, password)

    def _add(self, values=None, user=None, folio=None):
        return self.cards(user)._api_create((folio or self.folio).id, values or VISA)

    # Summary ---------------------------------------------------------------

    def test_summary_of_a_folio_without_card(self):
        summary = self.cards()._api_summary(self.folio.id)

        self.assertEqual(summary["status"], "none")
        self.assertIsNone(summary["source"])
        self.assertIsNone(summary["card"])
        self.assertTrue(summary["passwordConfigured"])
        self.assertEqual(summary["permissions"], {"canView": True, "canAdd": True})

    def test_summary_says_when_the_property_has_no_password(self):
        self.vault.unlink()

        summary = self.cards(self.viewer)._api_summary(self.folio.id)

        self.assertFalse(summary["passwordConfigured"])
        self.assertEqual(summary["permissions"], {"canView": True, "canAdd": False})

    def test_folio_of_another_property_is_forbidden(self):
        other_property = self.env["pms.property"].create(
            {
                "name": "Other property",
                "company_id": self.company1.id,
                "default_pricelist_id": self.pricelist1.id,
            }
        )
        self.clerk.write(
            {
                "pms_property_ids": [(6, 0, other_property.ids)],
                "pms_property_id": other_property.id,
            }
        )
        cards = self.cards(self.clerk)
        self.refused(CardForbidden, lambda: cards._api_summary(self.folio.id))
        self.refused(FolioNotFound, lambda: cards._api_summary(self.folio.id + 10**6))

    # Manual cards ----------------------------------------------------------

    def test_add_a_card(self):
        summary = self._add()

        self.assertEqual(summary["status"], "available")
        self.assertEqual(
            summary["card"], {"brand": "visa", "last4": "4242", "holder": "Jane Doe"}
        )
        self.assertEqual(summary["source"]["type"], "manual")
        self.assertEqual(summary["source"]["createdBy"], self.receptionist.name)
        self.assertTrue(summary["source"]["createdAt"])
        self.assertIsNone(summary["availableUntil"])
        card = self.env["pms.folio.payment.card"].search(
            [("folio_id", "=", self.folio.id)]
        )
        self.assertNotIn(b"4242424242424242", base64.b64decode(card.payload))
        self.assertEqual(self.logs().mapped("result"), ["success"])

    def test_add_needs_no_password_but_refuses_a_second_card(self):
        self._add()

        self.refused(CardAlreadyExists, lambda: self._add(dict(VISA, holder="Other")))

        self.assertEqual(self._details(PASSWORD)["holder"], "Jane Doe")

    def test_add_validates_the_card(self):
        error = self.refused(
            CardValidationError, lambda: self._add(dict(VISA, pan="4242424242424241"))
        )
        self.assertEqual(error.to_json()["code"], "validation_error")
        self.assertTrue(error.description)
        self.refused(
            CardValidationError, lambda: self._add(dict(VISA, expiryMonth="3"))
        )
        self.refused(
            CardValidationError, lambda: self.cards()._api_create(self.folio.id, None)
        )
        self.assertEqual(self.logs().mapped("result"), ["validation_error"] * 3)

    def test_add_needs_the_group_and_a_password_configured(self):
        self.refused(CardForbidden, lambda: self._add(user=self.viewer))
        self.vault.unlink()
        self.refused(CardPasswordNotConfigured, self._add)

    def test_details(self):
        self._add()

        details = self._details(PASSWORD, user=self.viewer)

        self.assertEqual(
            details,
            {
                "holder": "Jane Doe",
                "pan": "4242424242424242",
                "expiryMonth": 3,
                "expiryYear": VISA["expiryYear"],
                "cvv": "012",
                "brand": "visa",
            },
        )

    def test_details_need_the_group(self):
        self._add()
        self.refused(CardForbidden, lambda: self._details(PASSWORD, user=self.clerk))

    def test_details_of_a_folio_without_card(self):
        self.refused(CardNotAvailable, lambda: self._details(PASSWORD))

    def test_edit_only_what_changes(self):
        self._add()

        summary = self.cards()._api_update(
            self.folio.id, PASSWORD, {"holder": "Jane A. Doe"}
        )

        self.assertEqual(summary["card"]["holder"], "Jane A. Doe")
        details = self._details(PASSWORD)
        self.assertEqual(details["holder"], "Jane A. Doe")
        self.assertEqual(details["pan"], "4242424242424242")

    def test_edit_checks_the_result(self):
        self._add()

        def edit(values, user=None):
            return lambda: self.cards(user)._api_update(self.folio.id, PASSWORD, values)

        self.refused(CardValidationError, edit({"expiryMonth": 4}))
        self.refused(CardValidationError, edit({"cvv": "1"}))
        self.refused(CardValidationError, edit({}))
        self.refused(CardForbidden, edit({"holder": "X"}, user=self.viewer))

    def test_delete(self):
        self._add()
        self.refused(
            InvalidCardPassword,
            lambda: self.cards()._api_delete(self.folio.id, "wrong password"),
        )

        summary = self.cards()._api_delete(self.folio.id, PASSWORD)

        self.assertEqual(summary["status"], "none")
        self.assertFalse(
            self.env["pms.folio.payment.card"].search(
                [("folio_id", "=", self.folio.id)]
            )
        )

    # Password --------------------------------------------------------------

    def test_three_wrong_passwords_lock_the_folio_for_five_minutes(self):
        self._add()
        now = fields.Datetime.now()
        with self._lock_now(now):
            for _attempt in range(3):
                self.refused(
                    InvalidCardPassword, lambda: self._details("wrong password")
                )
            self.refused(CardPasswordLocked, lambda: self._details(PASSWORD))
            # The lock is per user: someone else can still look
            self._details(PASSWORD, user=self.viewer)
            # Adding needs no password, so it is never locked
            self.refused(CardAlreadyExists, self._add)
        with self._lock_now(now + datetime.timedelta(minutes=6)):
            self._details(PASSWORD)
        mine = self.logs().filtered(lambda log: log.user_id == self.receptionist)
        self.assertEqual(
            mine.sorted("id").mapped("result"),
            [
                "success",
                "invalid_card_password",
                "invalid_card_password",
                "invalid_card_password",
                "card_password_locked",
                "card_already_exists",
                "success",
            ],
        )

    def test_a_right_password_resets_the_count(self):
        self._add()
        for _round in range(2):
            for _attempt in range(2):
                self.refused(
                    InvalidCardPassword, lambda: self._details("wrong password")
                )
            self._details(PASSWORD)

    def test_failing_across_folios_locks_the_property(self):
        other_folio = self._make_folio()
        log = self.env["pms.payment.card.access.log"].with_user(self.receptionist)
        for _attempt in range(10):
            log._log("view_details", "invalid_card_password", folio=other_folio)

        self.refused(CardPasswordLocked, lambda: self._details(PASSWORD))

    def test_change_and_reset_the_password(self):
        self._add()

        self.vault._change_password(PASSWORD, "another password")

        self.assertEqual(self._details("another password")["pan"], "4242424242424242")
        self.vault._reset_password("a third password")
        self.assertEqual(
            self.cards()._api_summary(self.folio.id)["status"],
            "none",
            "A reset loses the cards the previous key pair sealed",
        )

    # Channel cards ---------------------------------------------------------

    def test_a_channel_card_before_anyone_looks_at_it(self):
        self.add_channel_card(
            vcc_valid_from=datetime.date(2026, 11, 10),
            vcc_valid_until=datetime.date(2026, 12, 10),
            vcc_balance=120.5,
            vcc_currency_id=self.env.ref("base.EUR").id,
        )

        summary = self.cards()._api_summary(self.folio.id)

        self.assertEqual(summary["status"], "available")
        self.assertIsNone(summary["card"])
        self.assertEqual(summary["source"]["type"], "channel")
        self.assertEqual(summary["source"]["channel"], "Booking.com")
        self.assertEqual(summary["source"]["externalReference"], "4093826715")
        self.assertTrue(summary["availableUntil"])
        self.assertEqual(
            summary["virtualCard"],
            {
                "chargeableFrom": "2026-11-10",
                "chargeableUntil": "2026-12-10",
                "balance": {"amount": 120.5, "currency": "EUR"},
            },
        )
        self.refused(CardAlreadyExists, self._add)

    def test_a_channel_card_is_fetched_and_never_stored(self):
        card = self.add_channel_card()
        patcher, calls = self._patch_fetch()

        with patcher:
            details = self._details(PASSWORD)

        self.assertEqual(calls, [card])
        self.assertEqual(details["pan"], "5555555555554444")
        self.assertEqual(details["brand"], "mastercard")
        self.assertEqual(details["cvv"], "")
        self.assertFalse(card.payload)
        summary = self.cards()._api_summary(self.folio.id)
        self.assertEqual(
            summary["card"],
            {"brand": "mastercard", "last4": "4444", "holder": "John Smith"},
        )

    def test_a_channel_card_the_channel_manager_deleted(self):
        card = self.add_channel_card()
        patcher, _calls = self._patch_fetch(error=CardNotAvailable("gone"))

        with patcher:
            self.refused(CardNotAvailable, lambda: self._details(PASSWORD))

        self.assertEqual(card.state, "not_available")
        summary = self.cards()._api_summary(self.folio.id)
        self.assertEqual(summary["status"], "not_available_in_channel")
        self.assertIsNone(summary["card"])
        # Once it is gone, a card can be added by hand
        self.assertEqual(self._add()["source"]["type"], "manual")

    def test_a_channel_card_past_its_day(self):
        yesterday = fields.Date.today() - datetime.timedelta(days=1)
        self.add_channel_card(available_until=yesterday)

        summary = self.cards()._api_summary(self.folio.id)

        self.assertEqual(summary["status"], "not_available_in_channel")

    def test_an_unreachable_channel_manager_is_not_a_wrong_password(self):
        self.add_channel_card()
        patcher, calls = self._patch_fetch(error=ChannelManagerUnavailable("down"))
        with patcher:
            for _attempt in range(4):
                self.refused(ChannelManagerUnavailable, lambda: self._details(PASSWORD))
        self.assertEqual(len(calls), 4, "A 502 never turns into a lock")

    def test_a_channel_card_cannot_be_edited_but_can_be_dismissed(self):
        card = self.add_channel_card()
        self.refused(
            CardValidationError,
            lambda: self.cards()._api_update(self.folio.id, PASSWORD, {"holder": "X"}),
        )

        summary = self.cards()._api_delete(self.folio.id, PASSWORD)

        self.assertEqual(summary["status"], "none")
        self.assertEqual(card.state, "dismissed")
        self.add_channel_card()
        self.assertEqual(card.state, "dismissed", "The same booking stays dismissed")
        self.add_channel_card(channel_code="1700000002")
        self.assertEqual(card.state, "available", "A new booking brings it back")

    # Retention -------------------------------------------------------------

    def test_purge(self):
        old_folio = self._make_folio(days_ago=30)
        self._add(folio=old_folio)
        self._add()
        expired = self.add_channel_card(
            folio=self._make_folio(days_ago=30),
            available_until=fields.Date.today() - datetime.timedelta(days=10),
            last4="4444",
            holder="John Smith",
        )

        self.env["pms.folio.payment.card"]._cron_purge()

        summary = self.cards()._api_summary
        self.assertEqual(summary(old_folio.id)["status"], "none")
        self.assertEqual(summary(self.folio.id)["status"], "available")
        self.assertFalse(expired.last4)

    # Password wizard -------------------------------------------------------

    def test_the_wizard_never_stores_the_passwords(self):
        other_property = self.env["pms.property"].create(
            {
                "name": "Property without password",
                "company_id": self.company1.id,
                "default_pricelist_id": self.pricelist1.id,
            }
        )
        wizards = self.env["pms.payment.card.password.wizard"]
        wizard = wizards.create(
            {
                "pms_property_id": other_property.id,
                "operation": "set",
                "new_password": "brand new password",
                "confirm_password": "brand new password",
            }
        )

        self.assertFalse(wizard.new_password)
        self.assertFalse(wizard.confirm_password)
        self.assertTrue(other_property.payment_card_password_configured)
        vault = self.env["pms.payment.card.vault"]._get_for_property(other_property)
        vault._unlock("brand new password")
        with self.assertRaises(UserError):
            wizards.create(
                {
                    "pms_property_id": other_property.id,
                    "operation": "change",
                    "current_password": "not the password",
                    "new_password": "another new password",
                    "confirm_password": "another new password",
                }
            )
