import json
import logging
import random
import time
from datetime import timedelta

from psycopg2 import OperationalError

from odoo import _, api, fields, models
from odoo.service.model import PG_CONCURRENCY_ERRORS_TO_RETRY

from .pms_neobookings_backend import (
    DATETIME_FORMAT,
    PAYMENT_ENDPOINT,
    NeobookingsError,
    NeobookingsUncertainError,
)

_logger = logging.getLogger(__name__)

# Minutes to wait before the first retry; it doubles on every new failure
RETRY_DELAY_MINUTES = 5
# Requests sent for one payment in a run: a reversal and the new payment
MAX_REQUESTS_PER_RUN = 3
# Tries to store the answer of Neobookings when other transactions change
# the same record
MAX_WRITE_TRIES = 5
REVERSAL_SUFFIX = " (reversal)"


class PmsNeobookingsPaymentSync(models.Model):
    """What Neobookings knows about one PMS payment.

    ``sent_data`` keeps the order and payment Neobookings counts now. Each
    sync compares it with what should be reported for the payment (the part
    of it that fits in the amount of the Neobookings reservation) and sends
    one request at a time: a payment no longer posted (or deleted) is
    reversed with the same amount and the opposite sign, and a changed
    payment is reversed and sent again. Resetting a payment to draft and
    posting it again unchanged sends nothing.

    Every request is stored in ``inflight_data`` and committed before it is
    sent. When the result is unknown (no answer, server error, process cut
    off) the sync waits in "To Review" until a person tells whether
    Neobookings got it, because sending it again could count it twice.
    """

    _name = "pms.neobookings.payment.sync"
    _description = "Neobookings Payment Sync"
    _order = "id desc"
    _rec_name = "payment_name"

    backend_id = fields.Many2one(
        comodel_name="pms.neobookings.backend",
        string="Neobookings Connection",
        required=True,
        ondelete="cascade",
        index=True,
    )
    payment_id = fields.Many2one(
        comodel_name="account.payment",
        string="Payment",
        ondelete="set null",
        index=True,
    )
    payment_name = fields.Char(string="Payment Reference", readonly=True)
    folio_id = fields.Many2one(
        comodel_name="pms.folio",
        string="Folio",
        ondelete="set null",
        readonly=True,
        index=True,
    )
    pms_property_id = fields.Many2one(
        comodel_name="pms.property",
        string="Property",
        ondelete="set null",
        readonly=True,
    )
    state = fields.Selection(
        selection=[
            ("pending", "Pending"),
            ("sending", "In Progress"),
            ("review", "To Review"),
            ("simulated", "Simulated"),
            ("done", "Done"),
            ("error", "Error"),
        ],
        required=True,
        default="pending",
        index=True,
    )
    attempts = fields.Integer(readonly=True)
    next_attempt_date = fields.Datetime(readonly=True)
    last_sync_date = fields.Datetime(readonly=True)
    last_error = fields.Text(readonly=True)
    sent_data = fields.Text(
        string="Data in Neobookings",
        readonly=True,
        help="Order and payment Neobookings counts now.",
    )
    inflight_data = fields.Text(
        string="Request without Answer",
        readonly=True,
        help="Request being sent, or whose answer never arrived, and the "
        "data Neobookings counts if it received it.",
    )
    last_request = fields.Text(readonly=True)
    last_response = fields.Text(readonly=True)

    _sql_constraints = [
        (
            "payment_backend_unique",
            "unique(payment_id, backend_id)",
            "A payment can only be synchronized once per Neobookings connection.",
        )
    ]

    @api.model
    def _safe_track_payments(self, payments, unlinked_syncs=None):
        """Track ``payments`` without ever blocking the payment itself.

        A failure is logged and the payment goes on; only the concurrency
        errors are raised, so that the request is retried as usual.
        """
        try:
            with self.env.cr.savepoint():
                if unlinked_syncs is not None:
                    unlinked_syncs.exists()._mark_pending()
                else:
                    self._track_payments(payments)
        except Exception as error:
            if (
                isinstance(error, OperationalError)
                and error.pgcode in PG_CONCURRENCY_ERRORS_TO_RETRY
            ):
                raise
            _logger.exception(
                "Neobookings: payments %s could not be tracked", payments.ids
            )

    @api.model
    def _track_payments(self, payments):
        """Flag ``payments`` to be reported to Neobookings."""
        if not payments:
            return
        backends = self.env["pms.neobookings.backend"].sudo().search([])
        if not backends:
            return
        payments = payments.sudo()
        syncs = self.sudo().search([("payment_id", "in", payments.ids)])
        for sync in syncs:
            # Keep the folio up to date: changes in it are watched by the cron
            folio = sync.backend_id._get_payment_folio(sync.payment_id)
            if folio and folio != sync.folio_id:
                sync.write(
                    {"folio_id": folio.id, "pms_property_id": folio.pms_property_id.id}
                )
        syncs._mark_pending()
        for payment in payments - syncs.payment_id:
            if payment.state != "posted":
                continue
            for backend in backends:
                folio = backend._get_payment_folio(payment)
                if folio:
                    self.sudo().create(
                        {
                            "backend_id": backend.id,
                            "payment_id": payment.id,
                            "payment_name": payment.name,
                            "folio_id": folio.id,
                            "pms_property_id": folio.pms_property_id.id,
                            "next_attempt_date": fields.Datetime.now(),
                        }
                    )
                    break

    def _mark_pending(self):
        now = fields.Datetime.now()
        syncs = self.filtered(
            # A request without answer must be checked by a person first
            lambda sync: sync.state != "review"
            # Already waiting to be sent: avoid writing for nothing
            and not (
                sync.state == "pending"
                and not sync.attempts
                and not sync.last_error
                and (not sync.next_attempt_date or sync.next_attempt_date <= now)
            )
        )
        if syncs:
            syncs.write(
                {
                    "state": "pending",
                    "attempts": 0,
                    "next_attempt_date": fields.Datetime.now(),
                    "last_error": False,
                }
            )

    # What to send

    def _get_desired_data(self):
        """Order and payment Neobookings should count, or None."""
        self.ensure_one()
        payment = self.payment_id.sudo().exists()
        if not payment or payment.state != "posted":
            return None
        backend = self.backend_id
        folio = backend._get_payment_folio(payment)
        if not folio:
            return None
        # Only the part that fits in the amount of the Neobookings reservation
        quantity = backend._get_folio_allocation(folio).get(payment.id, 0.0)
        if payment.currency_id.is_zero(quantity):
            return None
        return {
            "order": folio.external_reference,
            "payment": dict(
                backend._prepare_payment_data(payment, folio), Quantity=quantity
            ),
        }

    def _prepare_reversal(self, data):
        """Request that cancels ``data``, already counted by Neobookings."""
        self.ensure_one()
        payment = data["payment"]
        now = self.backend_id._format_datetime(
            fields.Datetime.now(), self.pms_property_id
        )
        return {
            "order": data["order"],
            "payment": dict(
                payment,
                DateCreated=now.strftime(DATETIME_FORMAT),
                Quantity=-payment["Quantity"],
                Description=payment["Description"] + REVERSAL_SUFFIX,
            ),
        }

    def _get_sync_actions(self):
        """Requests needed to bring Neobookings up to date.

        :return: list of ``(request_data, sent_data)`` tuples in sending
            order, where ``sent_data`` is what Neobookings counts once it
            gets the request.
        """
        self.ensure_one()
        desired = self._get_desired_data()
        current = json.loads(self.sent_data) if self.sent_data else None
        if desired == current:
            return []
        actions = []
        if current:
            actions.append((self._prepare_reversal(current), None))
        if desired:
            actions.append((desired, desired))
        return actions

    # Sending

    def _send(self):
        """Send the requests of this payment, one at a time.

        Each request is committed as in progress before sending it and its
        result right after, so a request is never sent twice by mistake.
        The actions are computed again after each request, in case the
        payment changed meanwhile.
        """
        self.ensure_one()
        backend = self.backend_id
        auto_commit = backend._is_auto_commit()
        for _index in range(MAX_REQUESTS_PER_RUN):
            if auto_commit:
                self.env.invalidate_all()
            if self.state != "pending" or self.inflight_data:
                return
            actions = self._get_sync_actions()
            if not actions:
                self._write_result(self._get_done_vals())
                return
            request_data, sent_data = actions[0]
            payload = backend._prepare_order_payment(request_data)
            if not self._claim(payload, sent_data):
                return
            try:
                response = backend._request(PAYMENT_ENDPOINT, payload)
            except NeobookingsUncertainError as error:
                self._write_result(self._get_review_vals(str(error)))
                return
            except NeobookingsError as error:
                self._write_result(self._get_failed_vals(str(error)))
                return
            self._write_result(
                {
                    "state": "pending",
                    "sent_data": json.dumps(sent_data) if sent_data else False,
                    "inflight_data": False,
                    "attempts": 0,
                    "next_attempt_date": fields.Datetime.now(),
                    "last_error": False,
                    "last_response": json.dumps(response),
                    "last_sync_date": fields.Datetime.now(),
                }
            )

    def _claim(self, payload, sent_data):
        """Store the request as in progress and commit it.

        :return: False when another transaction changed the record meanwhile;
            it stays pending and is computed again on the next run.
        """
        self.ensure_one()
        vals = {
            "state": "sending",
            "inflight_data": json.dumps({"request": payload, "sent_data": sent_data}),
            "last_request": json.dumps(payload),
        }
        if not self.backend_id._is_auto_commit():
            self.write(vals)
            return True
        try:
            self.write(vals)
            self.env.cr.commit()  # pylint: disable=invalid-commit
        except OperationalError as error:
            if error.pgcode not in PG_CONCURRENCY_ERRORS_TO_RETRY:
                raise
            self.env.cr.rollback()
            self.env.invalidate_all()
            return False
        return True

    def _write_result(self, vals):
        """Write and commit ``vals``, retrying on concurrent updates.

        The result of a request already sent must not be lost: if it cannot
        be stored, the request stays in progress and goes to review.
        """
        self.ensure_one()
        if not self.backend_id._is_auto_commit():
            self.write(vals)
            return
        for tries in range(1, MAX_WRITE_TRIES + 1):
            try:
                self.write(vals)
                self.env.cr.commit()  # pylint: disable=invalid-commit
                return
            except OperationalError as error:
                if (
                    error.pgcode not in PG_CONCURRENCY_ERRORS_TO_RETRY
                    or tries == MAX_WRITE_TRIES
                ):
                    raise
                self.env.cr.rollback()
                self.env.invalidate_all()
                time.sleep(random.uniform(0.1, 0.5) * tries)

    def _get_done_vals(self):
        return {
            "state": "done",
            "attempts": 0,
            "next_attempt_date": False,
            "last_error": False,
            "last_sync_date": fields.Datetime.now(),
        }

    def _get_failed_vals(self, message):
        """The request was not received: retry later, or stop in error."""
        self.ensure_one()
        now = fields.Datetime.now()
        attempts = self.attempts + 1
        vals = {
            "inflight_data": False,
            "attempts": attempts,
            "last_error": message,
            "last_sync_date": now,
        }
        if attempts >= self.backend_id.max_attempts:
            vals.update({"state": "error", "next_attempt_date": False})
        else:
            delay = RETRY_DELAY_MINUTES * 2 ** (attempts - 1)
            vals.update(
                {
                    "state": "pending",
                    "next_attempt_date": now + timedelta(minutes=delay),
                }
            )
        return vals

    def _get_review_vals(self, message):
        """The request may have been received: keep it for a person."""
        return {
            "state": "review",
            "next_attempt_date": False,
            "last_error": message,
            "last_sync_date": fields.Datetime.now(),
        }

    def _set_interrupted(self):
        for sync in self:
            _logger.warning(
                "Neobookings: request of %s cut off, left to review",
                sync.payment_name,
            )
            sync.write(
                sync._get_review_vals(
                    _(
                        "The request was cut off before its answer arrived; "
                        "Neobookings may have received it."
                    )
                )
            )

    def _handle_unexpected_error(self, message):
        """Store an unexpected error after rolling back the transaction."""
        self.ensure_one()
        if self.inflight_data:
            vals = self._get_review_vals(message)
        else:
            vals = self._get_failed_vals(message)
        self._write_result(vals)

    def _simulate_sync_actions(self, actions):
        """Store the requests that would be sent, without sending them."""
        self.ensure_one()
        if not actions:
            self.write(self._get_done_vals())
            return
        backend = self.backend_id
        payloads = [
            backend._prepare_order_payment(request_data)
            for request_data, _sent_data in actions
        ]
        self.write(
            {
                "state": "simulated",
                "attempts": 0,
                "next_attempt_date": False,
                "last_error": False,
                "last_request": json.dumps(payloads),
                "last_response": False,
                "last_sync_date": fields.Datetime.now(),
            }
        )

    # Actions

    def action_retry(self):
        """Send again the pending payments or the ones in error."""
        self.filtered(lambda sync: sync.state in ("pending", "error"))._mark_pending()
        self.backend_id._trigger_sync()
        return True

    def action_mark_received(self):
        """Neobookings got the request without answer: count it as sent."""
        for sync in self.filtered(
            lambda sync: sync.state == "review" and sync.inflight_data
        ):
            sent_data = json.loads(sync.inflight_data)["sent_data"]
            sync.write(
                {
                    "sent_data": json.dumps(sent_data) if sent_data else False,
                    "inflight_data": False,
                    "state": "pending",
                    "attempts": 0,
                    "next_attempt_date": fields.Datetime.now(),
                    "last_error": False,
                }
            )
        self.backend_id._trigger_sync()
        return True

    def action_mark_not_received(self):
        """Neobookings did not get the request without answer: send it again."""
        self.filtered(lambda sync: sync.state == "review").write(
            {
                "inflight_data": False,
                "state": "pending",
                "attempts": 0,
                "next_attempt_date": fields.Datetime.now(),
                "last_error": False,
            }
        )
        self.backend_id._trigger_sync()
        return True
