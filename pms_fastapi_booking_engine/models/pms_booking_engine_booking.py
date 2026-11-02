# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import logging
import re
import secrets
from urllib.parse import urlencode, urlsplit, urlunsplit

from odoo import _, api, fields, models
from odoo.tools import consteq

_logger = logging.getLogger(__name__)

BOOKING_ENGINE_ENDPOINT = "pms_fastapi_booking_engine.fastapi_endpoint_booking_engine"


class PmsBookingEngineBooking(models.Model):
    """A booking made through the web booking engine: the folio it created,
    the credential of the guest and the hold while the payment is made."""

    _name = "pms.booking.engine.booking"
    _description = "Booking Engine Booking"
    _order = "id desc"
    _rec_name = "public_id"

    public_id = fields.Char(
        string="Public ID",
        help="Opaque id the booking engine knows the booking by.",
        required=True,
        readonly=True,
        copy=False,
        index=True,
        default=lambda self: "bk_" + secrets.token_urlsafe(12),
    )
    access_token = fields.Char(
        help="Credential of the guest to read the booking and pay it.",
        required=True,
        readonly=True,
        copy=False,
        groups="base.group_system",
        default=lambda self: secrets.token_urlsafe(32),
    )
    folio_id = fields.Many2one(
        string="Folio",
        comodel_name="pms.folio",
        required=True,
        readonly=True,
        index=True,
        ondelete="cascade",
    )
    pms_property_id = fields.Many2one(
        string="Property",
        related="folio_id.pms_property_id",
        store=True,
        index=True,
    )
    state = fields.Selection(
        selection=[
            ("pending_payment", "Pending Payment"),
            ("confirmed", "Confirmed"),
            ("expired", "Expired"),
        ],
        required=True,
        readonly=True,
        default="pending_payment",
        index=True,
    )
    expires_at = fields.Datetime(
        string="Hold Expires At",
        help="The reservations are released if the payment is not done by then.",
        readonly=True,
    )
    idempotency_key = fields.Char(readonly=True, copy=False)
    request_hash = fields.Char(
        help="Hash of the creation request, to answer its retries the same way.",
        readonly=True,
        copy=False,
    )
    return_url = fields.Char(
        string="Return URL",
        help="Page of the booking engine the guest comes back to from the gateway.",
        readonly=True,
    )
    language = fields.Char(help="Language the guest booked in (BCP 47).")
    holder_first_name = fields.Char()
    holder_last_name = fields.Char()
    holder_email = fields.Char()
    holder_phone = fields.Char()
    holder_country = fields.Char(help="ISO 3166-1 alpha-2 country code.")
    arrival_time = fields.Char()
    requests = fields.Text()
    marketing_opt_in = fields.Boolean(string="Accepts Marketing")
    terms_accepted_at = fields.Datetime(readonly=True)
    terms_ip = fields.Char(string="Terms Accepted From", readonly=True)
    preview_data = fields.Text(
        help="Price, rooms and payment plan the guest booked, as JSON.",
        readonly=True,
    )
    transaction_ids = fields.One2many(
        string="Transactions",
        comodel_name="payment.transaction",
        inverse_name="booking_engine_booking_id",
        readonly=True,
    )

    _sql_constraints = [
        ("public_id_unique", "unique(public_id)", "The public id must be unique."),
        (
            "idempotency_key_unique",
            "unique(pms_property_id, idempotency_key)",
            "The idempotency key was already used for this property.",
        ),
    ]

    @api.model
    def _find_by_token(self, public_id, token):
        """The booking with that id if the token is its credential."""
        booking = self.search([("public_id", "=", public_id)], limit=1)
        if booking and token and consteq(booking.access_token, token):
            return booking
        return self.browse()

    def _get_holder_name(self):
        self.ensure_one()
        return " ".join(
            name for name in (self.holder_first_name, self.holder_last_name) if name
        )

    # Payment

    def _create_transaction(self, provider, amount, idempotency_key=False):
        """Transaction to charge the amount through the provider."""
        self.ensure_one()
        folio = self.folio_id
        return self.env["payment.transaction"].create(
            {
                "provider_id": provider.id,
                "reference": self._get_transaction_reference(provider),
                "amount": amount,
                "currency_id": folio.currency_id.id,
                "partner_id": (
                    folio.partner_id or self.env.ref("pms.various_pms_partner")
                ).id,
                "operation": "online_redirect",
                "folio_ids": [(6, 0, folio.ids)],
                "booking_engine_booking_id": self.id,
                "booking_engine_idempotency_key": idempotency_key,
            }
        )

    def _get_transaction_reference(self, provider):
        """Up to 12 characters, the first 4 digits, as gateways like Redsys
        require: the digits of the folio, then random ones. Gateways reject an
        order number used before, and test merchants are shared by everybody."""
        digits = re.sub(r"\D", "", self.folio_id.name or "")[-8:].rjust(8, "0")
        return self.env["payment.transaction"]._compute_reference(
            provider.code, prefix=digits + secrets.token_hex(2).upper()
        )

    def _get_payment_return_url(self, result):
        """Route of the booking engine the gateway sends the guest back to; it
        redirects to the return URL of the booking."""
        self.ensure_one()
        endpoint = self.env.ref(BOOKING_ENGINE_ENDPOINT, raise_if_not_found=False)
        root_path = endpoint.root_path if endpoint else "/booking-engine/v1"
        query = urlencode({"result": result})
        return (
            f"{self.pms_property_id.get_base_url()}{root_path}"
            f"/bookings/{self.public_id}/payment-return?{query}"
        )

    def _get_guest_return_url(self, result):
        """Return URL of the booking with the booking id and the result."""
        self.ensure_one()
        scheme, netloc, path, query, fragment = urlsplit(self.return_url)
        params = urlencode({"bookingId": self.public_id, "payment": result})
        query = f"{query}&{params}" if query else params
        return urlunsplit((scheme, netloc, path, query, fragment))

    def _on_payment_done(self, transaction):
        for booking in self:
            if booking.state == "confirmed":
                continue
            if booking.state == "expired":
                booking.folio_id.message_post(
                    body=_(
                        "Payment %(reference)s of %(amount)s %(currency)s received "
                        "after the booking engine hold expired. Review the booking "
                        "and refund the payment if it cannot be kept."
                    )
                    % {
                        "reference": transaction.reference,
                        "amount": transaction.amount,
                        "currency": transaction.currency_id.name,
                    }
                )
                continue
            booking._confirm()

    # Lifecycle

    def _confirm(self):
        """Confirm the reservations of the hold and send the confirmation."""
        for booking in self:
            reservations = booking.folio_id.reservation_ids.filtered(
                lambda reservation: reservation.state == "draft"
            )
            reservations.action_confirm()
            # The tourist tax is not computed in draft, and confirming does not
            # compute it.
            for reservation in reservations:
                commands = reservation._compute_tourist_tax_lines()
                if commands:
                    reservation.write({"service_ids": commands})
            booking.write({"state": "confirmed", "expires_at": False})
            booking._send_confirmation_mail()

    def _send_confirmation_mail(self):
        self.ensure_one()
        folio = self.folio_id
        template = folio.pms_property_id.property_confirmed_template
        if not template or not folio.email:
            return
        try:
            with self.env.cr.savepoint():
                template.send_mail(
                    folio.id,
                    email_values={
                        "email_to": folio.email,
                        "email_from": folio.pms_property_id.email or False,
                        "auto_delete": False,
                    },
                )
        except Exception:
            # The booking is confirmed anyway: the mail can be sent by hand
            _logger.exception(
                "Booking engine: confirmation mail of %s not sent", folio.name
            )

    def _expire(self):
        """Release the reservations of the hold, without penalty: it never was
        a booking the guest could cancel."""
        for booking in self:
            reservations = booking.folio_id.reservation_ids.filtered(
                lambda reservation: reservation.state != "cancel"
            )
            # The PMS charges no penalty for a reservation cancelled because it
            # was modified.
            reservations.with_context(modified=True).action_cancel()
            reservations.write({"cancelled_reason": "intime"})
            booking.state = "expired"
            booking.folio_id.message_post(
                body=_("The booking engine hold expired before the payment was done.")
            )

    @api.model
    def _cron_expire_holds(self):
        """Release the holds whose payment did not arrive in time. A payment
        still in progress at the gateway keeps its hold until it ends."""
        bookings = self.search(
            [
                ("state", "=", "pending_payment"),
                ("expires_at", "<", fields.Datetime.now()),
            ]
        )
        for booking in bookings:
            if booking.transaction_ids.filtered(
                lambda tx: tx.state in ("pending", "authorized", "done")
            ):
                continue
            try:
                with self.env.cr.savepoint():
                    booking._expire()
            except Exception:
                _logger.exception(
                    "Booking engine: hold of %s not released", booking.folio_id.name
                )
