# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""Card number rules shared by the API and the channel manager imports.

They mirror the checks the front already makes before sending a card, so
both sides accept and reject the same numbers. The front is a convenience;
these are the ones that count.
"""

import re

BRANDS = (
    "visa",
    "mastercard",
    "amex",
    "discover",
    "diners",
    "jcb",
    "unionpay",
    "maestro",
    "unknown",
)
PAN_LENGTHS = {
    "visa": (13, 16, 19),
    "mastercard": (16,),
    "amex": (15,),
    "discover": (16, 17, 18, 19),
    "diners": (14, 15, 16, 17, 18, 19),
    "jcb": (16, 17, 18, 19),
    "unionpay": (16, 17, 18, 19),
    "maestro": (12, 13, 14, 15, 16, 17, 18, 19),
    "unknown": (12, 13, 14, 15, 16, 17, 18, 19),
}
MAX_EXPIRY_YEARS_AHEAD = 20
HOLDER_MAX_LENGTH = 128


def normalize_pan(pan):
    return re.sub(r"\D", "", pan or "")


def _starts_within(digits, size, low, high):
    return len(digits) >= size and low <= int(digits[:size]) <= high


def detect_brand(pan):
    digits = normalize_pan(pan)
    if digits.startswith(("34", "37")):
        return "amex"
    if digits.startswith("62"):
        return "unionpay"
    if _starts_within(digits, 4, 3528, 3589):
        return "jcb"
    if _starts_within(digits, 3, 300, 305) or digits.startswith(("36", "38", "39")):
        return "diners"
    if digits.startswith(("6011", "65")) or _starts_within(digits, 3, 644, 649):
        return "discover"
    if _starts_within(digits, 2, 51, 55) or _starts_within(digits, 4, 2221, 2720):
        return "mastercard"
    if digits.startswith("4"):
        return "visa"
    if digits.startswith(("50", "56", "57", "58", "59", "6")):
        return "maestro"
    return "unknown"


def luhn_ok(pan):
    digits = normalize_pan(pan)
    if not digits:
        return False
    total = 0
    for position, char in enumerate(reversed(digits)):
        value = int(char)
        if position % 2:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def cvv_length(brand):
    return 4 if brand == "amex" else 3


def expiry_error(month, year, today):
    """Return the error key of an expiry date, or None when it is valid."""
    if not isinstance(month, int) or not isinstance(year, int):
        return "expiryInvalidMonth"
    if not 1 <= month <= 12 or year < 1000:
        return "expiryInvalidMonth"
    months_ahead = year * 12 + month - (today.year * 12 + today.month)
    if months_ahead < 0:
        return "expiryExpired"
    if months_ahead > MAX_EXPIRY_YEARS_AHEAD * 12:
        return "expiryTooFar"
    return None


def card_error(card, today):
    """Return the key of the first rule ``card`` breaks, or None.

    ``card`` holds ``holder``, ``pan``, ``expiry_month``, ``expiry_year`` and
    ``cvv`` already parsed. The keys are the ones the front uses.
    """
    holder = (card.get("holder") or "").strip()
    if not holder:
        return "holderRequired"
    if len(holder) > HOLDER_MAX_LENGTH:
        return "holderTooLong"
    pan = card.get("pan") or ""
    brand = detect_brand(pan)
    if not pan:
        return "panRequired"
    if not pan.isdigit() or len(pan) not in PAN_LENGTHS[brand]:
        return "panInvalidLength"
    if not luhn_ok(pan):
        return "panInvalidChecksum"
    error = expiry_error(card.get("expiry_month"), card.get("expiry_year"), today)
    if error:
        return error
    cvv = card.get("cvv") or ""
    if not cvv:
        return "cvvRequired"
    if not cvv.isdigit() or len(cvv) != cvv_length(brand):
        return "cvvInvalidLength"
    return None
