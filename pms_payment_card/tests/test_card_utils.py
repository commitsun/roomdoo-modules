# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import datetime

from odoo.tests import BaseCase

from .. import card_utils

TODAY = datetime.date(2026, 11, 9)


def _card(**overrides):
    card = {
        "holder": "Jane Doe",
        "pan": "4242424242424242",
        "expiry_month": 3,
        "expiry_year": 2028,
        "cvv": "012",
    }
    card.update(overrides)
    return card


class TestCardUtils(BaseCase):
    def test_brands(self):
        cases = {
            "4242424242424242": "visa",
            "5555555555554444": "mastercard",
            "2223003122003222": "mastercard",
            "378282246310005": "amex",
            "6011111111111117": "discover",
            "30569309025904": "diners",
            "3530111333300000": "jcb",
            "6200000000000005": "unionpay",
            "5018000000000009": "maestro",
            "9999999999999995": "unknown",
        }
        for pan, brand in cases.items():
            self.assertEqual(card_utils.detect_brand(pan), brand, pan)

    def test_a_valid_card_has_no_error(self):
        self.assertIsNone(card_utils.card_error(_card(), TODAY))
        amex = _card(pan="378282246310005", cvv="1234")
        self.assertIsNone(card_utils.card_error(amex, TODAY))

    def test_errors(self):
        cases = [
            (_card(holder=" "), "holderRequired"),
            (_card(holder="x" * 129), "holderTooLong"),
            (_card(pan=""), "panRequired"),
            (_card(pan="424242424242"), "panInvalidLength"),
            (_card(pan="4242x42424242424"), "panInvalidLength"),
            (_card(pan="4242424242424241"), "panInvalidChecksum"),
            (_card(expiry_month=13), "expiryInvalidMonth"),
            (_card(expiry_year=28), "expiryInvalidMonth"),
            (_card(expiry_month=10, expiry_year=2026), "expiryExpired"),
            (_card(expiry_year=2047), "expiryTooFar"),
            (_card(cvv=""), "cvvRequired"),
            (_card(cvv="12"), "cvvInvalidLength"),
            (_card(pan="378282246310005", cvv="123"), "cvvInvalidLength"),
        ]
        for card, error in cases:
            self.assertEqual(card_utils.card_error(card, TODAY), error, card)

    def test_the_current_month_has_not_expired(self):
        card = _card(expiry_month=11, expiry_year=2026)
        self.assertIsNone(card_utils.card_error(card, TODAY))
