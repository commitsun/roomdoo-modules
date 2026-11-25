import logging
import re
import secrets

from psycopg2 import OperationalError

from odoo import api, fields, models
from odoo.service.model import PG_CONCURRENCY_ERRORS_TO_RETRY

_logger = logging.getLogger(__name__)

CODE_PREFIX = "RDP-"
# Without the characters easily mistaken for others (0/O, 1/I)
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8
CODE_RE = re.compile(rf"\b{re.escape(CODE_PREFIX)}[{CODE_ALPHABET}]{{{CODE_LENGTH}}}\b")


class PmsNeobookingsPaymentRequest(models.Model):
    """One request sent to Neobookings for a payment.

    Neobookings sends the whole reservation back after any change, with all
    its payments, the ones the PMS reported included. Each request carries a
    unique code at the start of its description, and Neobookings sends that
    description back in the name of the transaction: the payments with one of
    these codes were reported by the PMS, so they are not registered again.

    A request sent again because it was not received keeps its code, so a
    request Neobookings counted twice shows up as duplicated.
    """

    _name = "pms.neobookings.payment.request"
    _description = "Neobookings Payment Request"
    _order = "id desc"
    _rec_name = "code"

    sync_id = fields.Many2one(
        comodel_name="pms.neobookings.payment.sync",
        string="Payment Sync",
        required=True,
        readonly=True,
        ondelete="cascade",
        index=True,
    )
    backend_id = fields.Many2one(
        related="sync_id.backend_id",
        store=True,
        index=True,
    )
    folio_id = fields.Many2one(
        comodel_name="pms.folio",
        string="Folio",
        readonly=True,
        ondelete="set null",
        index=True,
    )
    code = fields.Char(readonly=True, index=True, copy=False)
    quantity = fields.Float(readonly=True)
    description = fields.Char(
        readonly=True,
        help="Description sent to Neobookings, after the code.",
    )
    state = fields.Selection(
        selection=[
            ("sending", "In Progress"),
            ("review", "To Review"),
            ("done", "Received"),
            ("failed", "Not Received"),
        ],
        string="Status",
        required=True,
        readonly=True,
        default="sending",
    )
    neobookings_reference = fields.Char(
        string="Neobookings Payment",
        readonly=True,
        index=True,
        help="Id of the payment in Neobookings, known when Neobookings sends "
        "the reservation back.",
    )
    neobookings_duplicate_references = fields.Char(
        string="Duplicated in Neobookings",
        readonly=True,
        help="Other payments of Neobookings with the code of this request: "
        "Neobookings counted it more than once.",
    )
    echo_date = fields.Datetime(string="Sent Back On", readonly=True)

    _sql_constraints = [
        (
            "code_unique",
            "unique(code)",
            "The code of a Neobookings payment request must be unique.",
        )
    ]

    @api.model
    def _new_code(self):
        return CODE_PREFIX + "".join(
            secrets.choice(CODE_ALPHABET) for _index in range(CODE_LENGTH)
        )

    @api.model
    def _get_description(self, description, code):
        """Description to send: the code goes first, so that it survives if
        Neobookings shortens the text."""
        return f"{code} {description}" if code else description

    @api.model
    def _is_neobookings_echo(self, folio, transaction):
        """Whether ``transaction``, sent with ``folio`` by the calling API
        user, is a payment the PMS reported to Neobookings.

        It never blocks the update of the reservation: on an unexpected error
        the transaction is registered as usual.
        """
        try:
            with self.env.cr.savepoint():
                return self._match_echo(folio, transaction)
        except Exception as error:
            if (
                isinstance(error, OperationalError)
                and error.pgcode in PG_CONCURRENCY_ERRORS_TO_RETRY
            ):
                raise
            _logger.exception(
                "Neobookings: transaction %s of folio %s could not be checked",
                transaction.reference,
                folio.name,
            )
            return False

    @api.model
    def _match_echo(self, folio, transaction):
        requests = self.sudo().browse()
        domain = [("backend_id.api_user_id", "=", self.env.uid)]
        codes = CODE_RE.findall(transaction.name or "")
        if codes:
            requests = self.sudo().search(domain + [("code", "in", codes)], limit=1)
        # Once linked, the id of Neobookings is enough, even without the code
        if not requests and transaction.reference:
            requests = self.sudo().search(
                domain
                + [
                    ("folio_id", "=", folio.id),
                    ("neobookings_reference", "=", transaction.reference),
                ],
                limit=1,
            )
        if not requests:
            return False
        requests._register_echo(transaction.reference)
        return True

    def _register_echo(self, reference):
        """Neobookings sent back the payment of this request as ``reference``."""
        self.ensure_one()
        if not reference or reference == self.neobookings_reference:
            return
        if not self.neobookings_reference:
            self.write(
                {
                    "neobookings_reference": reference,
                    "echo_date": fields.Datetime.now(),
                }
            )
            # Proof that Neobookings got a request whose answer was lost
            if self.state == "review":
                self.sync_id._set_received(self)
            return
        duplicates = (self.neobookings_duplicate_references or "").split(", ")
        if reference in duplicates:
            return
        _logger.warning(
            "Neobookings: request %s of %s counted twice (%s and %s)",
            self.code,
            self.sync_id.payment_name,
            self.neobookings_reference,
            reference,
        )
        self.neobookings_duplicate_references = ", ".join(
            [ref for ref in duplicates if ref] + [reference]
        )
