# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging

from odoo.addons.component.core import Component

from ...components.adapter import MAX_PAGES

_logger = logging.getLogger(__name__)


class ChannelChannexBookingRevisionAdapter(Component):
    """Read side of the booking revisions feed.

    Not a binding of any PMS model: what it mirrors is the message Channex
    hands over, not a hotel entity.
    """

    _name = "channel.channex.booking.revision.adapter"
    _inherit = "channel.channex.adapter"
    _apply_on = "channel.channex.booking.revision"

    _resource = "booking_revisions"
    # An API key reaches every property of its account, so the property filter
    # is the scoping, not an optimisation.
    _server_filters = ("property_id",)

    def feed(self, property_id):
        """The revisions Channex still holds as undelivered, oldest first.

        Ordered on purpose: the revisions of one booking only mean anything
        applied in the order they were issued.

        Paginated on ``meta.total``, which this endpoint reports where the rest
        of the API reports ``total_pages``. Re-reading page 1 until it comes
        back empty is what the feed is designed for, but only once revisions
        are being acknowledged; until then it never empties.
        """
        records, page = [], 1
        limit = self.backend_record.page_limit
        while page <= MAX_PAGES:
            body = self.request(
                "GET",
                f"{self._resource}/feed",
                params={
                    "filter[property_id]": property_id,
                    "order[inserted_at]": "asc",
                    "pagination[page]": page,
                    "pagination[limit]": limit,
                },
            )
            data = (body or {}).get("data") or []
            records.extend(self._unwrap(item) for item in data)
            total = ((body or {}).get("meta") or {}).get("total")
            if not data or (total is not None and page * limit >= total):
                return records
            page += 1
        _logger.warning("Channex booking revisions feed hit the %s page cap", MAX_PAGES)
        return records
