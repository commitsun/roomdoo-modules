# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""Pure helpers to read what a channel reports as paid through WuBook.

Some channels (today only the Mirai booking engine) pass in the booking
ancillary data how much of the booking the guest already paid online and how
much is still due, as strings like '218.03 EUR' / '0.00 EUR', and an online
payment still pending as 'BTP|<local deadline>|Pending'.
"""

import datetime
import logging
import re

import pytz

from .channel_booking import get_split_info

_logger = logging.getLogger(__name__)

PAID_AMOUNT_KEY = "Paid amount"
DUE_AMOUNT_KEY = "Due amount"
PENDING_PAYMENT_KEY = "BTP"

# '<amount> <ISO currency>': dot decimal separator, at most 2 decimals and no
# thousands separator. Anything else is rejected instead of guessed.
AMOUNT_RE = re.compile(r"^\s*(\d+(?:\.\d{1,2})?)\s+([A-Z]{3})\s*$")
# 'BTP|YYYY-MM-DDTHH:MM:SS|Pending': the deadline is in the hotel local time
PENDING_PAYMENT_RE = re.compile(
    r"^\s*BTP\|(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\|Pending\s*$"
)


def parse_wubook_amount(text):
    """Return (amount, currency ISO code) from '<amount> <ISO>'.

    Return (None, None) when the text does not have exactly that shape.
    """
    if not isinstance(text, str):
        return None, None
    match = AMOUNT_RE.match(text)
    if not match:
        return None, None
    return float(match.group(1)), match.group(2)


def parse_wubook_btp(text):
    """Return the deadline of an online payment still pending, or None.

    The deadline is a naive datetime in the local time of the hotel.
    """
    if not isinstance(text, str):
        return None
    match = PENDING_PAYMENT_RE.match(text)
    if not match:
        return None
    try:
        return datetime.datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def local_to_utc(value, tz_name):
    """Convert a naive datetime in the ``tz_name`` time zone to naive UTC."""
    try:
        tz = pytz.timezone(tz_name or "UTC")
    except pytz.UnknownTimeZoneError:
        tz = pytz.utc
    return tz.localize(value).astimezone(pytz.utc).replace(tzinfo=None)


def channel_payment_values(booking, tz_name):
    """Return the folio binding values for what the booking reports as paid.

    Only bookings carrying the "Paid amount" ancillary are mapped; for the
    others an empty dict is returned so nothing is written.

    The amounts are those of the WHOLE booking, also when Wubook split it in
    several folios. A version reporting 0 paid and 0 due carries no
    information (the channel resets both when a paid booking is modified,
    and sends them while an online payment is pending), so the last known
    amounts are kept. ``tz_name`` is the time zone of the hotel, used to
    store the pending payment deadline in UTC.
    """
    ancillary = booking.get("ancillary")
    if not isinstance(ancillary, dict) or PAID_AMOUNT_KEY not in ancillary:
        return {}
    split = get_split_info(booking)
    values = {"channel_payment_split": f"{split[0]}/{split[1]}" if split else False}
    paid, currency = parse_wubook_amount(ancillary[PAID_AMOUNT_KEY])
    due, due_currency = parse_wubook_amount(ancillary.get(DUE_AMOUNT_KEY))
    if paid is None or due is None or currency != due_currency:
        _logger.warning(
            "Booking %s: unexpected paid / due amounts reported by the channel "
            "(%r / %r), ignored.",
            booking.get("reservation_code"),
            ancillary[PAID_AMOUNT_KEY],
            ancillary.get(DUE_AMOUNT_KEY),
        )
    elif paid + due > 0:
        values.update(
            {
                "channel_paid_amount": paid,
                "channel_due_amount": due,
                "channel_payment_currency": currency,
                "channel_payment_deadline": False,
            }
        )
    deadline = parse_wubook_btp(ancillary.get(PENDING_PAYMENT_KEY))
    if deadline:
        values["channel_payment_deadline"] = local_to_utc(deadline, tz_name)
    return values
