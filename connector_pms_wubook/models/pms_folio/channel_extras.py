# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""Pure helpers to turn the extras of a WuBook booking into service lines.

The extras (parking, cot, breakfast...) a guest buys with the booking come in
``addons_list``. No ORM here: what is done with them lives in the folio
binding (``binding_channel_extras.py``).
"""

import datetime
import re
import unicodedata

from .channel_booking import get_split_info

_NOT_ALPHANUMERIC = re.compile(r"[\W_]+")


def normalize_extra_name(name):
    """Return the key that identifies an extra by its name.

    Channels send the same extra with cosmetic differences over time (case,
    accents, punctuation), so the key folds them: casefolded, without accents
    and with every run of non alphanumeric characters turned into a single
    space. Digits are kept: "Parking 2" and "Parking" are different extras.
    """
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", str(name).casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return _NOT_ALPHANUMERIC.sub(" ", text).strip()


def parse_wubook_extras(booking):
    """Return the extras of a WuBook booking, one per key.

    Channel bookings send ``{name, req, price, descr}`` and the WuBook booking
    engine ``{id, name, number, perday, id_room, price}``. In both, ``price``
    is the TOTAL of the extra and ``req`` (``number``) its total quantity,
    already multiplied by the nights or the guests: whatever the WuBook docs
    say about a unit price, that is how the amounts the guests pay add up.

    Entries without a name or without a numeric price are skipped, a missing
    or zero quantity counts as one and an extra sent twice under the same key
    is aggregated.

    :return: list of ``{"key", "name", "qty", "amount"}`` dicts, in the order
             the channel sent them.
    """
    addons = (booking or {}).get("addons_list") or []
    if not isinstance(addons, list | tuple):
        return []
    extras = {}
    for addon in addons:
        if not isinstance(addon, dict):
            continue
        name = " ".join(str(addon.get("name") or "").split())
        key = normalize_extra_name(name)
        if not key:
            continue
        try:
            amount = round(float(addon.get("price")), 2)
        except (TypeError, ValueError):
            continue
        try:
            qty = int(addon.get("req") if "req" in addon else addon.get("number"))
        except (TypeError, ValueError):
            qty = 0
        qty = max(qty, 1)
        extra = extras.get(key)
        if extra:
            extra["qty"] += qty
            extra["amount"] = round(extra["amount"] + amount, 2)
        else:
            extras[key] = {"key": key, "name": name, "qty": qty, "amount": amount}
    return list(extras.values())


def read_channel_extras(booking):
    """Return what the folio binding keeps of the extras of ``booking``."""
    split = get_split_info(booking)
    return {
        "channel": booking.get("id_channel"),
        "currency": booking.get("currency") or None,
        "split": list(split) if split else None,
        "items": parse_wubook_extras(booking),
    }


def plan_service_lines(
    qty, amount, checkin, checkout, per_day, consumed_on, onboard_date=None
):
    """Spread ``qty`` units worth ``amount`` in total over a stay.

    The lines go where ``pms.service._compute_service_line_ids`` puts the ones
    of the product, with the quantity and the total the channel charged:

    * a per day product consumed before or after the night gets a line per
      night (one day later when consumed after): ``qty`` split evenly when it
      is a multiple of the nights, one unit on each of the first ``qty``
      nights when it is lower than the nights, else all of it on the first
      date;
    * any other product gets a single line on the checkin date when it is
      consumed on checkin, on the checkout date otherwise. A product that is
      not per day goes on ``onboard_date`` instead of the checkout date when
      given: the current date of the hotel, where pms charges those services
      once the guests are on board.

    The total is exact to the cent: when ``amount / qty`` is not a whole
    number of cents, one line of a single unit carries the whole amount.

    :return: list of ``(date, day_qty, price_unit)`` tuples.
    """
    qty = max(int(qty or 0), 1)
    amount = round(float(amount or 0.0), 2)
    nights = max((checkout - checkin).days, 1)
    if per_day and consumed_on in ("before", "after"):
        first = checkin + datetime.timedelta(days=1 if consumed_on == "after" else 0)
        dates = [first + datetime.timedelta(days=night) for night in range(nights)]
    elif consumed_on == "checkin":
        dates = [checkin]
    elif onboard_date and not per_day:
        dates = [onboard_date]
    else:
        dates = [checkout]
    price_unit = round(amount / qty, 2)
    if abs(round(price_unit * qty, 2) - amount) > 0.001:
        return [(dates[0], 1, amount)]
    if len(dates) == 1:
        return [(dates[0], qty, price_unit)]
    if qty % nights == 0:
        return [(date, qty // nights, price_unit) for date in dates]
    if qty < nights:
        return [(date, 1, price_unit) for date in dates[:qty]]
    return [(dates[0], qty, price_unit)]
