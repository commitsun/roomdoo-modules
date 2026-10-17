# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""Pure helpers to read what a channel passes through a WuBook booking."""

# Wubook id of the Mirai booking engine channel
MIRAI_CHANNEL_ID = 197


def get_split_info(booking):
    """Return (n_stream, tot_stream) when Wubook split the booking in streams.

    Wubook splits a booking it cannot hold as a single reservation (e.g. a
    different board per night) into several reservation codes, one folio
    each. Every stream carries the data of the WHOLE booking (extras, paid
    amount...), so whatever belongs to the booking must not be imported once
    per stream. Return None when the booking was not split.
    """
    split = (booking.get("channel_data") or {}).get("split_reservation")
    if not isinstance(split, dict):
        return None
    try:
        return int(split.get("n_stream") or 0), int(split.get("tot_stream") or 0)
    except (TypeError, ValueError):
        return None
