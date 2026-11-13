# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging

from odoo.addons.component.core import Component

from ...components.adapter import MAX_PAGES, NOT_FOUND, ChannexAPIError

_logger = logging.getLogger(__name__)


class ChannelChannexBookingRevisionAdapter(Component):
    """The booking messages: reading them, and acknowledging what was read.

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
        """What Channex has just issued for this property, oldest first.

        A page long and always fresh: a revision is offered here only for its
        first half hour, acknowledged or not. That makes this the reading to do
        on notice, and not the one to sweep with.
        """
        return self._collect(
            f"{self._resource}/feed",
            {
                # An API key reaches every property of its account, so the
                # property filter is the scoping, not an optimisation.
                "filter[property_id]": property_id,
                "order[inserted_at]": "asc",
            },
        )

    def pending(self, property_id):
        """Every message this property has left unacknowledged, however old.

        This listing keeps what the feed drops, which is exactly what a sweep is
        for: a message nobody could apply half an hour ago is still here.

        The status of each row is checked instead of trusted to the query, and
        that is not caution for its own sake: Channex answers a filter it does
        not know with everything rather than refusing it, so the day this filter
        stops being supported the answer would quietly include what is already
        acknowledged.
        """
        return [
            record
            for record in self._collect(
                self._resource,
                {
                    "filter[property_id]": property_id,
                    "filter[acknowledge_status]": "pending",
                    "order[inserted_at]": "asc",
                },
            )
            if record.get("acknowledge_status") == "pending"
        ]

    def _collect(self, path, params):
        """Every page of a listing that reports ``meta.total``.

        Both of these do, where the rest of the API reports ``total_pages``.
        Re-reading page one until it comes back empty is what Channex designed
        the feed for, but only once revisions are being acknowledged: a message
        that cannot be applied is never acknowledged, so it never empties.
        """
        records, page = [], 1
        limit = self.backend_record.page_limit
        while page <= MAX_PAGES:
            body = self.request(
                "GET",
                path,
                params={
                    **params,
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
        _logger.warning("Channex %s hit the %s page cap", path, MAX_PAGES)
        return records

    def ack(self, external_id):
        """Confirm the message is saved, so Channex stops handing it over.

        Returns whether the receipt actually went out: the call is skipped on a
        backend with exports disabled, and nobody may record a receipt that was
        never sent.

        A 404 is not a failure here. It means Channex no longer holds the
        message, which leaves us exactly where acknowledging would: it will not
        be offered again.
        """
        try:
            body = self.request("POST", f"{self._resource}/{external_id}/ack")
        except ChannexAPIError as error:
            if error.status_code != NOT_FOUND:
                raise
            _logger.info(
                "Channex no longer holds booking revision %s to acknowledge",
                external_id,
            )
            return True
        return body is not None
