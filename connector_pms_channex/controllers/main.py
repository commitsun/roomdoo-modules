# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging

from odoo import http
from odoo.http import request

from odoo.addons.queue_job.job import identity_exact

from ..components.adapter import SECRET_HEADER

_logger = logging.getLogger(__name__)


class ChannexWebhook(http.Controller):
    """Where Channex tells us a booking message is waiting."""

    @http.route(
        "/channex/webhook/<int:backend_id>",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def notify(self, backend_id, **kwargs):
        """Queue the reading and answer, nothing else.

        The body is never read. The webhook is registered with ``send_data``
        off, so it carries no booking, and a booking arriving inside an unsigned
        request is not something to write into a folio in any case: what gets
        queued is a reading of our own, made with our own key.

        Answered as fast as it can be, because Channex re-delivers a webhook
        that took a 5xx: doing the import here would buy a second delivery, not
        a better import.
        """
        backend = (
            request.env["channel.channex.backend"].sudo().browse(backend_id).exists()
        )
        secret = request.httprequest.headers.get(SECRET_HEADER) or ""
        if not backend or not backend._channex_webhook_admits(secret):
            # Which of the two was wrong is not said, and no retry is offered:
            # a secret that is wrong now will be wrong in ten hours too.
            _logger.warning(
                "Channex webhook refused for backend %s from %s",
                backend_id,
                request.httprequest.remote_addr,
            )
            return request.make_response(
                "forbidden", status=403, headers=[("Content-Type", "text/plain")]
            )
        backend.with_delay(identity_key=identity_exact).channex_import_booking_feed()
        return request.make_response("queued", headers=[("Content-Type", "text/plain")])
