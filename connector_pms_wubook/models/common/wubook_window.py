# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""The stretch of calendar Wubook accepts.

Restrictions and availability are both written night by night and both are
refused outside this window, so the exporters clip what they push to it.
"""
import datetime

from odoo import fields

# Wubook rejects updates older than 2 days and further ahead than ~2 years.
PAST_DAYS = 2
FUTURE_DAYS = 730


def accepted_window():
    """:return: the ``(first, last)`` nights Wubook takes, both included."""
    today = fields.Date.today()
    return (
        today - datetime.timedelta(days=PAST_DAYS),
        today + datetime.timedelta(days=FUTURE_DAYS),
    )
