# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import logging
import xmlrpc.client
from datetime import timedelta

from odoo import _, fields, models

from odoo.addons.pms_payment_card.exceptions import ChannelManagerUnavailable

_logger = logging.getLogger(__name__)

FETCH_METHOD = "fetch_ccard"
# Wubook anti-flood policy for fetch_ccard: 40 calls an hour
FETCH_MAX_CALLS = 40
FETCH_TIME_WINDOW = 3600
FETCH_TIMEOUT = 15
REDACTED = "[redacted]"


def _timeout_transport(url, timeout):
    base = (
        xmlrpc.client.SafeTransport
        if url.lower().startswith("https")
        else xmlrpc.client.Transport
    )

    class TimeoutTransport(base):
        def make_connection(self, host):
            connection = super().make_connection(host)
            connection.timeout = timeout
            return connection

    return TimeoutTransport()


class ChannelWubookBackend(models.Model):
    _inherit = "channel.wubook.backend"

    payment_card_fetch_enabled = fields.Boolean(
        string="Read payment cards",
        help="Let users with the card password see the cards Wubook keeps "
        "for the bookings of this backend.",
    )
    payment_card_password = fields.Char(
        string="Sealed card password",
        copy=False,
        groups="base.group_system",
        help="Card password of the Wubook account, sealed with the key pair of "
        "the property: only a request with the card password of the property "
        "opens it.",
    )
    payment_card_password_configured = fields.Boolean(
        string="Card password entered",
        compute="_compute_payment_card_password_configured",
    )

    def _compute_payment_card_password_configured(self):
        for record in self:
            record.payment_card_password_configured = bool(
                record.sudo().payment_card_password
            )

    def action_payment_card_password(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Wubook card password"),
            "res_model": "channel.wubook.backend.card.password.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_backend_id": self.id},
        }

    def _payment_card_fetch(self, booking_code, card_password):
        """Call ``fetch_ccard`` and return its ``(code, data)`` answer.

        It does not go through the adapter on purpose: ``_exec`` writes its
        arguments to the server log and ``channel.backend.log`` keeps both the
        arguments and the answer, so the card password and the card would end
        up in the database. The call still leaves a row in
        ``channel.backend.log``, with both blanked out, so that it counts
        towards the anti-flood limit.
        """
        self.ensure_one()
        method = self._payment_card_fetch_method()
        self._payment_card_check_rate(method)
        server = xmlrpc.client.ServerProxy(
            self.url, transport=_timeout_transport(self.url, FETCH_TIMEOUT)
        )
        try:
            token = self._payment_card_token(server)
            answer = server.fetch_ccard(
                token, self.property_code, booking_code, card_password
            )
        except (xmlrpc.client.Error, OSError, ValueError) as error:
            # Only the type: the message may quote the request
            _logger.warning(
                "Wubook %s failed for backend %s: %s",
                FETCH_METHOD,
                self.id,
                type(error).__name__,
            )
            self._payment_card_log_call(method, "error")
            raise ChannelManagerUnavailable(
                _("The channel manager does not answer. Try again later.")
            ) from None
        if not isinstance(answer, list | tuple) or len(answer) != 2:
            self._payment_card_log_call(method, "error")
            raise ChannelManagerUnavailable(
                _("The channel manager gave an unexpected answer.")
            )
        code, data = answer
        self._payment_card_log_call(method, code)
        return code, data

    def _payment_card_token(self, server):
        if self.permanent_token:
            return self.permanent_token
        code, token = server.acquire_token(self.username, self.password, self.pkey)
        if code:
            raise ChannelManagerUnavailable(
                _("The channel manager refused the credentials of this backend.")
            )
        return token

    def _payment_card_fetch_method(self):
        methods = self.env["channel.backend.method"].sudo()
        method = methods.search(
            [
                ("name", "=", FETCH_METHOD),
                ("backend_type_id", "=", self.backend_type_id.id),
            ],
            limit=1,
        )
        limits = {"max_calls": FETCH_MAX_CALLS, "time_window": FETCH_TIME_WINDOW}
        if not method:
            method = methods.create(
                dict(limits, name=FETCH_METHOD, backend_type_id=self.backend_type_id.id)
            )
        elif not method.max_calls or not method.time_window:
            method.write(limits)
        return method

    def _payment_card_check_rate(self, method):
        since = fields.Datetime.now() - timedelta(seconds=method.time_window)
        calls = (
            self.env["channel.backend.log"]
            .sudo()
            .search_count(
                [
                    ("backend_id", "=", self.parent_id.id),
                    ("method_id", "=", method.id),
                    ("timestamp", ">=", since),
                ]
            )
        )
        if calls >= method.max_calls:
            raise ChannelManagerUnavailable(
                _(
                    "The channel manager allows %(calls)s card requests an hour. "
                    "Try again later.",
                    calls=method.max_calls,
                )
            )

    def _payment_card_log_call(self, method, code):
        self.env["channel.backend.log"].sudo().create(
            {
                "backend_id": self.parent_id.id,
                "timestamp": fields.Datetime.now(),
                "method_id": method.id,
                "arguments": REDACTED,
                "response_code": code,
                "response": REDACTED,
            }
        )
