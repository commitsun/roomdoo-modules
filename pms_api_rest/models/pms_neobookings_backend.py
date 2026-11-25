import logging
import threading
import uuid
from datetime import datetime

import pytz
import requests
from urllib3.exceptions import MaxRetryError, NewConnectionError

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

PAYMENT_ENDPOINT = "OrderPaymentCreateRQ"
# Neobookings documents its date-times as "yyyy-MM-dd hh:mm:ss"
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S"
CONNECT_TIMEOUT = 10
FOLIO_CHANGE_MARGIN_MINUTES = 10
# Answers telling the request was not processed, so it can be sent again
REJECTED_HTTP_STATUSES = set(range(400, 500)) | {503}


class NeobookingsError(Exception):
    """Neobookings did not get or refused a request: it can be sent again."""


class NeobookingsUncertainError(NeobookingsError):
    """Neobookings may have processed the request despite the failure.

    Every request creates a new payment in Neobookings, so sending it again
    could count it twice: a person has to check it first.
    """


def _is_connection_failure(error):
    """Whether ``error`` happened before the request reached Neobookings."""
    if isinstance(error, requests.ConnectTimeout):
        return True
    if isinstance(error, requests.ConnectionError) and not isinstance(
        error, requests.exceptions.SSLError
    ):
        reason = error.args[0] if error.args else None
        # Refused connection or unknown host
        return isinstance(reason, MaxRetryError) and isinstance(
            reason.reason, NewConnectionError
        )
    return False


class PmsNeobookingsBackend(models.Model):
    """Connection used to report PMS payments to Neobookings.

    Neobookings charges some reservations through its own payment gateway and
    may cancel them for lack of payment. Reporting the payments registered in
    the PMS (``OrderPaymentCreateRQ`` of its public API) lets it know they are
    already paid. Only posted customer payments and refunds of a single folio
    created by the Neobookings API user are reported, for the enabled
    properties; never the ones created by that user (they came from
    Neobookings) nor the ones of the excluded journals.

    Neobookings records every request as a new payment and adds them all up,
    so a payment that changes or is cancelled is reversed by sending the same
    amount with the opposite sign. Only the cron sends requests.

    Neobookings must never count more than the amount of its reservation, so
    the PMS payments only fill what Neobookings has not charged itself: extras
    paid at the hotel are not reported (see ``_get_folio_allocation``).
    """

    _name = "pms.neobookings.backend"
    _description = "Neobookings Connection"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    dry_run = fields.Boolean(
        string="Simulation",
        default=True,
        help="Compute and store what would be sent, without calling "
        "Neobookings. The payments simulated are not sent when the "
        "simulation is turned off; only the ones changed afterwards.",
    )
    url = fields.Char(
        string="API URL",
        required=True,
        default="https://ws.neobookings.com/api/v2/",
        help="Base URL of the Neobookings public API, ending in /api/v2/. "
        "The test environment is https://ws-test.neobookings.com/api/v2/.",
    )
    language = fields.Char(
        required=True,
        default="es",
        help="Two-letter language code sent in every request.",
    )
    timeout = fields.Integer(
        string="Timeout (seconds)",
        required=True,
        default=30,
        help="Time to wait for the answer of Neobookings. Without an answer, "
        "the payment may have been received, so it waits for a person to "
        "check it instead of being sent again.",
    )
    max_attempts = fields.Integer(
        required=True,
        default=5,
        help="A failed send is retried, waiting longer each time, until this "
        "number of attempts is reached. Then it stays in error.",
    )
    api_user_id = fields.Many2one(
        comodel_name="res.users",
        string="Neobookings API User",
        required=True,
        domain=[("pms_api_client", "=", True)],
        help="User Neobookings uses to call the PMS API. Only the folios "
        "created by this user are known by Neobookings, and the payments "
        "created by this user came from Neobookings, so they are never sent "
        "back. Its external public token, the one used to send availability "
        "and prices, authenticates the requests.",
    )
    pms_property_ids = fields.Many2many(
        comodel_name="pms.property",
        string="Properties",
        help="Properties whose payments are reported to Neobookings.",
    )
    excluded_journal_ids = fields.Many2many(
        comodel_name="account.journal",
        string="Excluded Journals",
        help="Payments of these journals are never reported, e.g. the journal "
        "where the charges made by Neobookings are registered by hand. They "
        "count as already paid to Neobookings.",
    )
    sync_ids = fields.One2many(
        comodel_name="pms.neobookings.payment.sync",
        inverse_name="backend_id",
        string="Payment Syncs",
    )
    sync_pending_count = fields.Integer(
        string="Pending Payments", compute="_compute_sync_counts"
    )
    sync_review_count = fields.Integer(
        string="Payments to Review", compute="_compute_sync_counts"
    )
    sync_error_count = fields.Integer(
        string="Payments in Error", compute="_compute_sync_counts"
    )

    def _compute_sync_counts(self):
        groups = self.env["pms.neobookings.payment.sync"].read_group(
            [
                ("backend_id", "in", self.ids),
                ("state", "in", ["pending", "review", "error"]),
            ],
            ["backend_id", "state"],
            ["backend_id", "state"],
            lazy=False,
        )
        counts = {
            (group["backend_id"][0], group["state"]): group["__count"]
            for group in groups
        }
        for backend in self:
            backend.sync_pending_count = counts.get((backend.id, "pending"), 0)
            backend.sync_review_count = counts.get((backend.id, "review"), 0)
            backend.sync_error_count = counts.get((backend.id, "error"), 0)

    @api.constrains("dry_run", "api_user_id")
    def _check_token(self):
        for backend in self:
            if not backend.dry_run and not backend._get_token():
                raise ValidationError(
                    _(
                        "The Neobookings API user needs an external public "
                        "token before turning the simulation off."
                    )
                )

    @api.constrains("pms_property_ids", "active")
    def _check_unique_property(self):
        for backend in self.filtered("active"):
            other = self.search(
                [
                    ("id", "!=", backend.id),
                    ("pms_property_ids", "in", backend.pms_property_ids.ids),
                ],
                limit=1,
            )
            if other:
                shared = backend.pms_property_ids & other.pms_property_ids
                raise ValidationError(
                    _(
                        "The property %(property)s is already linked to the "
                        "Neobookings connection %(backend)s.",
                        property=shared[0].name,
                        backend=other.name,
                    )
                )

    # Payment selection and content

    def _get_payment_folio(self, payment):
        """Return the folio Neobookings knows ``payment`` belongs to.

        An empty recordset means the payment must not be reported.
        """
        self.ensure_one()
        no_folio = self.env["pms.folio"]
        folio = payment.folio_ids
        # A payment shared by several folios cannot be split per order
        if len(folio) != 1:
            return no_folio
        if (
            payment.payment_type not in ("inbound", "outbound")
            or payment.is_internal_transfer
            # Payments created by Neobookings itself are already there
            or payment.create_uid == self.api_user_id
            or payment.journal_id in self.excluded_journal_ids
            or folio.pms_property_id not in self.pms_property_ids
            # Only the folios Neobookings sent us carry its order reference
            or folio.create_uid != self.api_user_id
            or not folio.external_reference
        ):
            return no_folio
        return folio

    def _get_neobookings_amount(self, folio):
        """Amount of ``folio`` that came from Neobookings, at current prices.

        Reservations and services added in the PMS (extras) are left out.
        """
        self.ensure_one()
        api_user = self.api_user_id
        reservations = folio.reservation_ids.filtered(
            lambda reservation: reservation.create_uid == api_user
        )
        services = folio.service_ids.filtered(
            lambda service: service.create_uid == api_user
        )
        return sum(reservations.mapped("price_total")) + sum(
            services.mapped("price_total")
        )

    def _get_neobookings_paid(self, folio):
        """Net amount of ``folio`` Neobookings already counts as paid.

        That is the payments created by its API user and the ones registered
        in the excluded journals, which are never reported.
        """
        self.ensure_one()
        paid = 0.0
        for payment in folio.payment_ids:
            if (
                payment.state != "posted"
                or len(payment.folio_ids) != 1
                or payment.is_internal_transfer
                or payment.payment_type not in ("inbound", "outbound")
            ):
                continue
            if (
                payment.create_uid == self.api_user_id
                or payment.journal_id in self.excluded_journal_ids
            ):
                sign = -1 if payment.payment_type == "outbound" else 1
                paid += sign * payment.amount
        return paid

    def _get_folio_allocation(self, folio):
        """Quantity to report for each PMS payment of ``folio``, by payment id.

        The PMS payments fill, in date order, what Neobookings has not
        charged itself up to the amount of its reservation, so a payment may
        be reported only in part, or not at all (extras). Refunds are reported
        in full, as long as Neobookings does not end up counting less than
        nothing for the folio.
        """
        self.ensure_one()
        folio = folio.sudo()
        currency = folio.currency_id or self.env.company.currency_id
        syncs = (
            self.env["pms.neobookings.payment.sync"]
            .sudo()
            .search(
                [
                    ("backend_id", "=", self.id),
                    ("payment_id", "in", folio.payment_ids.ids),
                ]
            )
        )
        payments = syncs.payment_id.filtered(
            lambda payment: payment.state == "posted"
            and self._get_payment_folio(payment) == folio
        )
        refunds = payments.filtered(
            lambda payment: payment.payment_type == "outbound"
        ).sorted(lambda payment: (payment.date, payment.id))
        inbound = (payments - refunds).sorted(
            lambda payment: (payment.date, payment.id)
        )
        neo_paid = self._get_neobookings_paid(folio)
        available = self._get_neobookings_amount(folio) - neo_paid
        budget = currency.round(
            max(
                0.0,
                min(
                    sum(inbound.mapped("amount")),
                    available + sum(refunds.mapped("amount")),
                ),
            )
        )
        allocation = {}
        for payment in inbound:
            quantity = currency.round(min(payment.amount, budget))
            allocation[payment.id] = quantity
            budget = currency.round(budget - quantity)
        refund_budget = currency.round(sum(allocation.values()) + max(neo_paid, 0.0))
        for payment in refunds:
            quantity = currency.round(min(payment.amount, refund_budget))
            allocation[payment.id] = -quantity
            refund_budget = currency.round(refund_budget - quantity)
        return allocation

    @api.model
    def _format_datetime(self, value, pms_property):
        """``value`` (naive UTC) in the time zone of ``pms_property``."""
        tz = pytz.timezone(pms_property.tz or "UTC")
        return pytz.utc.localize(value).astimezone(tz)

    @api.model
    def _get_payment_method(self, payment):
        """Neobookings payment method of ``payment``.

        Cash journals are cash, the online payments of the PMS (payment link,
        pre check-in) are card terminal payments and the rest is other.
        """
        if payment.journal_id.type == "cash":
            return "cash"
        if "payment_transaction_id" in payment._fields and (
            payment.payment_transaction_id
        ):
            return "tpv"
        return "other"

    def _prepare_payment_data(self, payment, folio):
        """Content of the Payment element, without the Removed flag."""
        created = self._format_datetime(payment.create_date, folio.pms_property_id)
        date_created = datetime.combine(payment.date, created.time())
        sign = -1 if payment.payment_type == "outbound" else 1
        return {
            "DateCreated": date_created.strftime(DATETIME_FORMAT),
            "Method": self._get_payment_method(payment),
            "Quantity": payment.currency_id.round(sign * payment.amount),
            "Currency": payment.currency_id.name,
            "Description": payment.name,
        }

    # Neobookings API

    def _get_token(self):
        self.ensure_one()
        return self.api_user_id.sudo().external_public_token

    def _prepare_request_header(self):
        return {
            "RequestId": str(uuid.uuid4()),
            "Timestamp": fields.Datetime.now().strftime(TIMESTAMP_FORMAT),
            "Language": self.language,
        }

    def _prepare_order_payment(self, data, code=None):
        # The folio reference may be a Neobookings order or a channel
        # reservation (e.g. Booking.com). Neobookings looks for the value in
        # OrderId first and then in OrderIdOrigin, so both carry it.
        # Removed only marks a payment as not made: it does not cancel a
        # previous one, so it is never used.
        # The code of the request goes in the description, so that the
        # payment is recognized when Neobookings sends it back.
        payment = dict(data["payment"], Removed=False)
        payment["Description"] = self.env[
            "pms.neobookings.payment.request"
        ]._get_description(payment["Description"], code)
        return {
            "Request": self._prepare_request_header(),
            "OrderId": data["order"],
            "OrderIdOrigin": data["order"],
            "Payment": payment,
        }

    def _request(self, endpoint, payload):
        """Send ``payload`` and return the answer of Neobookings.

        :raise NeobookingsUncertainError: when the request may have been
            processed (no answer, server error...).
        :raise NeobookingsError: when it surely was not.
        """
        self.ensure_one()
        token = self._get_token()
        if not token:
            raise NeobookingsError(
                _("The Neobookings API user has no external public token.")
            )
        url = "{}/{}".format(self.url.rstrip("/"), endpoint)
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        }
        try:
            response = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=(CONNECT_TIMEOUT, self.timeout),
            )
        except requests.RequestException as error:
            if _is_connection_failure(error):
                raise NeobookingsError(
                    _("Neobookings could not be reached: %s", error)
                ) from error
            raise NeobookingsUncertainError(
                _(
                    "Neobookings did not answer; the payment may have been "
                    "received: %s",
                    error,
                )
            ) from error
        try:
            data = response.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            data = {}
        errors = (data.get("Response") or {}).get("Error") or []
        if isinstance(errors, dict):
            errors = [errors]
        if response.status_code == 200 and data and not errors:
            return data
        details = "; ".join(
            "{}: {}".format(error.get("Code"), error.get("Description"))
            for error in errors
            if isinstance(error, dict)
        )
        message = _(
            "Neobookings answered %(status)s: %(details)s",
            status=response.status_code,
            details=details or (response.text or "")[:500],
        )
        if errors or response.status_code in REJECTED_HTTP_STATUSES:
            raise NeobookingsError(message)
        raise NeobookingsUncertainError(message)

    # Synchronization

    @api.model
    def _is_auto_commit(self):
        return not getattr(threading.current_thread(), "testing", False)

    @api.model
    def _cron_sync_payments(self, limit=200):
        # Only this cron sends payments, so a request still in progress
        # when it starts was cut off: its result is unknown.
        self.env["pms.neobookings.payment.sync"].search(
            [("inflight_data", "!=", False), ("state", "!=", "review")]
        )._set_interrupted()
        if self._is_auto_commit():
            self.env.cr.commit()  # pylint: disable=invalid-commit
        for backend in self.search([]):
            backend._mark_changed_folios()
            backend._sync_pending(limit=limit)

    def _mark_changed_folios(self):
        """Compute again the payments of the folios changed since their sync.

        What may be reported for a payment depends on the whole folio: the
        other payments, the charges of Neobookings and the reservation amount.
        Any of them changes the folio (its stored totals), so its write date
        tells which ones to check. A margin covers the transactions committed
        after the sync that started before it.
        """
        self.ensure_one()
        self.env.flush_all()
        self.env.cr.execute(
            """
            SELECT sync.id
            FROM pms_neobookings_payment_sync sync
            JOIN pms_folio folio ON folio.id = sync.folio_id
            WHERE sync.backend_id = %s
                AND sync.state IN ('done', 'simulated')
                AND folio.write_date > sync.last_sync_date - %s * interval '1 minute'
            """,
            (self.id, FOLIO_CHANGE_MARGIN_MINUTES),
        )
        sync_ids = [row[0] for row in self.env.cr.fetchall()]
        self.env["pms.neobookings.payment.sync"].browse(sync_ids)._mark_pending()
        if sync_ids and self._is_auto_commit():
            self.env.cr.commit()  # pylint: disable=invalid-commit

    def _sync_pending(self, limit=None, force=False):
        """Send the pending payments of this connection.

        :param force: also send the ones still waiting for their next retry.
        """
        self.ensure_one()
        domain = [
            ("backend_id", "=", self.id),
            ("state", "=", "pending"),
            ("inflight_data", "=", False),
        ]
        if not force:
            domain += [
                "|",
                ("next_attempt_date", "=", False),
                ("next_attempt_date", "<=", fields.Datetime.now()),
            ]
        syncs = self.env["pms.neobookings.payment.sync"].search(
            domain, order="id", limit=limit
        )
        auto_commit = self._is_auto_commit()
        for sync in syncs:
            if self.dry_run:
                sync._simulate_sync_actions(sync._get_sync_actions())
                if auto_commit:
                    self.env.cr.commit()  # pylint: disable=invalid-commit
                continue
            try:
                sync._send()
            except Exception as error:
                if not auto_commit:
                    raise
                _logger.exception(
                    "Neobookings: unexpected error sending %s", sync.payment_name
                )
                self.env.cr.rollback()
                self.env.invalidate_all()
                sync._handle_unexpected_error(str(error))

    def _trigger_sync(self):
        """Ask the cron to send the pending payments as soon as possible."""
        cron = self.env.ref(
            "pms_api_rest.ir_cron_neobookings_sync_payments", raise_if_not_found=False
        )
        if cron:
            cron.sudo()._trigger()

    def action_sync_now(self):
        self.env["pms.neobookings.payment.sync"].search(
            [("backend_id", "in", self.ids), ("state", "=", "pending")]
        ).write({"next_attempt_date": fields.Datetime.now()})
        self._trigger_sync()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "info",
                "message": _("The pending payments will be sent in a moment."),
            },
        }

    def _action_view_syncs(self, state):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "pms_api_rest.pms_neobookings_payment_sync_action"
        )
        action["domain"] = [("backend_id", "=", self.id), ("state", "=", state)]
        action["context"] = {}
        return action

    def action_view_pending_syncs(self):
        return self._action_view_syncs("pending")

    def action_view_review_syncs(self):
        return self._action_view_syncs("review")

    def action_view_error_syncs(self):
        return self._action_view_syncs("error")
