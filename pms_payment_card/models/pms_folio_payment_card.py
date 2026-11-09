# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import json
import re
from contextlib import contextmanager
from datetime import timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import AccessDenied, AccessError

from odoo.addons.pms_api_rest.pms_api_rest_utils import pms_api_check_access

from .. import card_crypto, card_utils
from ..exceptions import (
    CardAlreadyExists,
    CardForbidden,
    CardNotAvailable,
    CardPasswordLocked,
    CardPasswordNotConfigured,
    CardValidationError,
    ChannelManagerUnavailable,
    FolioNotFound,
    InvalidCardPassword,
    PaymentCardError,
)

# Field names of the API and their names here
API_CARD_FIELDS = {
    "holder": "holder",
    "pan": "pan",
    "expiryMonth": "expiry_month",
    "expiryYear": "expiry_year",
    "cvv": "cvv",
}
BRAND_LABELS = {
    "visa": "Visa",
    "mastercard": "Mastercard",
    "amex": "American Express",
    "discover": "Discover",
    "diners": "Diners Club",
    "jcb": "JCB",
    "unionpay": "UnionPay",
    "maestro": "Maestro",
    "unknown": "Unknown",
}


class PmsFolioPaymentCard(models.Model):
    """The payment card of a folio, as reception sees it.

    A folio has at most one manual card, added in Roomdoo and kept sealed with
    the key pair of its property, and at most one channel card. A channel card
    holds no card data at all: the channel manager keeps the card and it is
    fetched from there every time someone asks to see it. After the first
    time, its brand, last digits and holder are kept so that the summary can
    show them.

    Everything goes through the ``_api_*`` methods, which check the folio, the
    permissions and the card password, and leave an access log.
    """

    _name = "pms.folio.payment.card"
    _description = "Folio payment card"

    folio_id = fields.Many2one(
        comodel_name="pms.folio",
        required=True,
        ondelete="cascade",
        index=True,
    )
    pms_property_id = fields.Many2one(
        related="folio_id.pms_property_id",
        store=True,
        index=True,
    )
    source_type = fields.Selection(
        selection=[
            ("manual", "Added in Roomdoo"),
            ("channel", "Channel manager"),
        ],
        required=True,
    )
    state = fields.Selection(
        selection=[
            ("available", "Available"),
            ("not_available", "No longer in the channel manager"),
            ("dismissed", "Dismissed"),
        ],
        required=True,
        default="available",
    )
    brand = fields.Selection(selection=list(BRAND_LABELS.items()))
    last4 = fields.Char(string="Last digits")
    holder = fields.Char()
    payload = fields.Char(
        help="Card data of a manual card, sealed with the key pair of the property.",
    )
    provider = fields.Char(
        help="Technical name of the channel manager that keeps the card.",
    )
    channel_code = fields.Char(
        help="Code of the booking in the channel manager.",
    )
    channel_name = fields.Char(string="Channel")
    reservation_id = fields.Many2one(
        comodel_name="pms.reservation",
        ondelete="set null",
        help="Reservation that brought the card.",
    )
    external_reference = fields.Char(
        help="Booking code in the channel that sold it.",
    )
    available_until = fields.Date(
        help="Day the channel manager deletes the card.",
    )
    vcc_valid_from = fields.Date(string="Virtual card chargeable from")
    vcc_valid_until = fields.Date(string="Virtual card chargeable until")
    vcc_balance = fields.Float(string="Virtual card balance")
    vcc_currency_id = fields.Many2one(
        comodel_name="res.currency",
        string="Virtual card currency",
    )

    _sql_constraints = [
        (
            "folio_source_unique",
            "unique(folio_id, source_type)",
            "A folio has at most one card of each source.",
        ),
    ]

    # API -------------------------------------------------------------------

    @api.model
    def _api_summary(self, folio_id):
        return self._summary(self._api_get_folio(folio_id))

    @api.model
    def _api_details(self, folio_id, password):
        with self._audited("view_details", folio_id) as folio:
            self._check_permission(view=True)
            private_key = self._unlock(folio, password)
            card = self._available_card(folio)
            return card._reveal(private_key)

    @api.model
    def _api_create(self, folio_id, values):
        with self._audited("add", folio_id) as folio:
            self._check_permission(add=True)
            vault = self._get_vault(folio)
            card = self._current_card(folio)
            if card and card._status() == "available":
                raise CardAlreadyExists(_("This folio already has a card."))
            data = self._parse_card(values)
            self._validate_card(data)
            card = self.sudo().create({"folio_id": folio.id, "source_type": "manual"})
            card._store(vault, data)
            return self._summary(folio)

    @api.model
    def _api_update(self, folio_id, password, values):
        with self._audited("edit", folio_id) as folio:
            self._check_permission(view=True, add=True)
            private_key = self._unlock(folio, password)
            card = self._available_card(folio)
            if card.source_type != "manual":
                raise CardValidationError(
                    _(
                        "A card from the channel manager cannot be edited. "
                        "Delete it and add a new one."
                    )
                )
            changes = self._parse_card(values, partial=True)
            data = dict(card._open_payload(private_key), **changes)
            self._validate_card(data)
            card._store(self._get_vault(folio), data)
            return self._summary(folio)

    @api.model
    def _api_delete(self, folio_id, password):
        with self._audited("delete", folio_id) as folio:
            self._check_permission(add=True)
            self._unlock(folio, password)
            card = self._current_card(folio)
            if not card:
                raise CardNotAvailable(_("This folio has no card."))
            if card.source_type == "manual":
                card.unlink()
            else:
                # The channel manager keeps it: it is only hidden here
                card.write(dict(self._no_masked_card(), state="dismissed"))
            return self._summary(folio)

    # Checks ----------------------------------------------------------------

    @contextmanager
    def _audited(self, action, folio_id):
        """Yield the folio and log the outcome of the action on it."""
        log = self.env["pms.payment.card.access.log"]
        folio = self.env["pms.folio"].sudo().browse(folio_id).exists()
        try:
            yield self._api_get_folio(folio_id)
        except PaymentCardError as error:
            log._log(action, error.code, folio=folio)
            raise
        log._log(action, "success", folio=folio)

    @api.model
    def _api_get_folio(self, folio_id):
        folio = self.env["pms.folio"].sudo().browse(folio_id).exists()
        if not folio:
            raise FolioNotFound(_("Folio not found."))
        try:
            pms_api_check_access(user=self.env.user, records=folio)
        except (AccessDenied, AccessError):
            raise CardForbidden(
                _("You are not allowed to access this folio.")
            ) from None
        return folio

    @api.model
    def _permissions(self):
        user = self.env.user
        return {
            "canView": user.has_group("pms_payment_card.group_payment_card_view"),
            "canAdd": user.has_group("pms_payment_card.group_payment_card_manage"),
        }

    @api.model
    def _check_permission(self, view=False, add=False):
        permissions = self._permissions()
        if (view and not permissions["canView"]) or (add and not permissions["canAdd"]):
            raise CardForbidden(_("You are not allowed to do this with payment cards."))

    @api.model
    def _get_vault(self, folio):
        vault = self.env["pms.payment.card.vault"]._get_for_property(
            folio.pms_property_id
        )
        if not vault:
            raise CardPasswordNotConfigured(
                _(
                    "This property has no card password yet. Ask an "
                    "administrator to create it."
                )
            )
        return vault

    @api.model
    def _unlock(self, folio, password):
        """Return the private key of the property of ``folio``."""
        vault = self._get_vault(folio)
        locks = self.env["pms.payment.card.lock"]
        if locks._is_locked(folio):
            raise CardPasswordLocked(
                _("Too many wrong card passwords. Try again in a few minutes.")
            )
        try:
            private_key = vault._unlock(password)
        except card_crypto.InvalidPassword:
            locks._register_failure(folio)
            raise InvalidCardPassword(_("The card password is not correct.")) from None
        locks._register_success(folio)
        return private_key

    # Cards of a folio ------------------------------------------------------

    @api.model
    def _current_card(self, folio):
        """The card the folio shows: the manual one goes first."""
        cards = self.sudo().search(
            [("folio_id", "=", folio.id), ("state", "!=", "dismissed")]
        )
        manual = cards.filtered(lambda card: card.source_type == "manual")
        return manual[:1] or cards[:1]

    @api.model
    def _available_card(self, folio):
        card = self._current_card(folio)
        if not card or card._status() != "available":
            raise CardNotAvailable(_("This folio has no card available."))
        return card

    def _status(self):
        self.ensure_one()
        if self.source_type == "channel":
            today = fields.Date.context_today(self)
            if self.state == "not_available" or (
                self.available_until and today > self.available_until
            ):
                return "not_available_in_channel"
        return "available"

    @api.model
    def _summary(self, folio):
        vault = self.env["pms.payment.card.vault"]._get_for_property(
            folio.pms_property_id
        )
        summary = {
            "status": "none",
            "source": None,
            "card": None,
            "availableUntil": None,
            "virtualCard": None,
            "passwordConfigured": bool(vault),
            "permissions": self._permissions(),
        }
        card = self._current_card(folio)
        if not card:
            return summary
        status = card._status()
        summary["status"] = status
        summary["source"] = card._source_json()
        if card.source_type == "channel" and card.available_until:
            summary["availableUntil"] = fields.Date.to_string(card.available_until)
        if status == "available":
            # A channel card nobody has looked at yet has no digits to show
            if card.last4:
                summary["card"] = {
                    "brand": card.brand or "unknown",
                    "last4": card.last4,
                    "holder": card.holder or "",
                }
            summary["virtualCard"] = card._virtual_card_json()
        return summary

    def _source_json(self):
        self.ensure_one()
        if self.source_type == "manual":
            source = {
                "type": "manual",
                "createdBy": self.create_uid.name,
                "createdAt": self._to_iso(self.create_date),
            }
        else:
            source = {
                "type": "channel",
                "channel": self.channel_name,
                "reservationId": self.reservation_id.id,
                "externalReference": self.external_reference,
                "createdAt": self._to_iso(self.create_date),
            }
        return {key: value for key, value in source.items() if value}

    def _to_iso(self, value):
        timezone = pytz.timezone(self.pms_property_id.tz or self.env.user.tz or "UTC")
        return pytz.utc.localize(value).astimezone(timezone).isoformat("T", "seconds")

    def _virtual_card_json(self):
        self.ensure_one()
        if not (self.vcc_valid_from or self.vcc_valid_until or self.vcc_currency_id):
            return None
        return {
            "chargeableFrom": fields.Date.to_string(self.vcc_valid_from) or None,
            "chargeableUntil": fields.Date.to_string(self.vcc_valid_until) or None,
            "balance": {
                "amount": self.vcc_balance,
                "currency": self.vcc_currency_id.name or "",
            },
        }

    # Card data -------------------------------------------------------------

    @api.model
    def _parse_card(self, values, partial=False):
        """Return the card fields of an API body, in the names used here."""
        if not isinstance(values, dict):
            raise CardValidationError(_("The card data is not valid."))
        data = {
            name: values[api_name]
            for api_name, name in API_CARD_FIELDS.items()
            if api_name in values
        }
        if partial:
            if not data:
                raise CardValidationError(_("There is nothing to change."))
            if ("expiry_month" in data) != ("expiry_year" in data):
                raise CardValidationError(
                    _("The expiry month and year have to be sent together.")
                )
        for name in ("holder", "pan", "cvv"):
            if name in data and not isinstance(data[name], str):
                raise CardValidationError(_("The card data is not valid."))
        for name in ("expiry_month", "expiry_year"):
            value = data.get(name)
            if name in data and (isinstance(value, bool) or not isinstance(value, int)):
                raise CardValidationError(_("The card data is not valid."))
        if "holder" in data:
            data["holder"] = data["holder"].strip()
        if "pan" in data:
            data["pan"] = re.sub(r"[\s-]", "", data["pan"])
        return data

    @api.model
    def _validation_messages(self):
        return {
            "holderRequired": _("Enter the name of the cardholder."),
            "holderTooLong": _("The name of the cardholder is too long."),
            "panRequired": _("Enter the card number."),
            "panInvalidLength": _("The card number has a wrong length."),
            "panInvalidChecksum": _("The card number is not valid."),
            "expiryInvalidMonth": _("The expiry date is not valid."),
            "expiryExpired": _("The card has expired."),
            "expiryTooFar": _("The expiry date cannot be more than 20 years ahead."),
            "cvvRequired": _("Enter the security code."),
            "cvvInvalidLength": _("The security code has a wrong length."),
        }

    @api.model
    def _validate_card(self, data):
        error = card_utils.card_error(data, fields.Date.context_today(self))
        if error:
            raise CardValidationError(self._validation_messages()[error])

    @api.model
    def _no_masked_card(self):
        return {"brand": False, "last4": False, "holder": False}

    def _masked_card(self, data):
        pan = card_utils.normalize_pan(data["pan"])
        return {
            "brand": card_utils.detect_brand(pan),
            "last4": pan[-4:],
            "holder": data["holder"],
        }

    def _store(self, vault, data):
        self.ensure_one()
        payload = json.dumps(
            {name: data[name] for name in API_CARD_FIELDS.values()}
        ).encode()
        self.write(dict(self._masked_card(data), payload=vault._seal(self, payload)))

    def _open_payload(self, private_key):
        self.ensure_one()
        vault = self.env["pms.payment.card.vault"]._get_for_property(
            self.pms_property_id
        )
        try:
            return json.loads(vault._open(private_key, self, self.payload))
        except card_crypto.SealedValueError:
            raise CardNotAvailable(
                _("The stored card cannot be read anymore.")
            ) from None

    def _reveal(self, private_key):
        self.ensure_one()
        if self.source_type == "manual":
            data = self._open_payload(private_key)
        else:
            data = self._fetch_channel_card(private_key)
            self.write(self._masked_card(data))
        pan = card_utils.normalize_pan(data["pan"])
        return {
            "holder": data["holder"],
            "pan": pan,
            "expiryMonth": data["expiry_month"],
            "expiryYear": data["expiry_year"],
            "cvv": data["cvv"] or "",
            "brand": card_utils.detect_brand(pan),
        }

    # Channel managers ------------------------------------------------------

    def _fetch_channel_card(self, private_key):
        """Ask the channel manager for the card, without keeping it.

        Each bridge implements ``_fetch_channel_card_<provider>``, which
        returns the same fields as a manual card or raises
        ``CardNotAvailable`` or ``ChannelManagerUnavailable``.
        """
        self.ensure_one()
        fetch = getattr(self, f"_fetch_channel_card_{self.provider}", None)
        if not self.provider or not fetch:
            raise ChannelManagerUnavailable(
                _("This channel manager cannot provide cards.")
            )
        try:
            return fetch(private_key)
        except CardNotAvailable:
            self.write(dict(self._no_masked_card(), state="not_available"))
            raise

    @api.model
    def _sync_channel_card(self, folio, values):
        """Record that a channel manager keeps a card for ``folio``.

        A new booking code means a new card, which shows up again even if the
        previous one had been dismissed.
        """
        card = self.sudo().search(
            [("folio_id", "=", folio.id), ("source_type", "=", "channel")], limit=1
        )
        if not card:
            return self.sudo().create(
                dict(values, folio_id=folio.id, source_type="channel")
            )
        if card.channel_code != values.get("channel_code"):
            values = dict(values, **self._no_masked_card(), state="available")
        card.write(values)
        return card

    # Retention -------------------------------------------------------------

    def _purge_date(self):
        self.ensure_one()
        checkouts = self.folio_id.reservation_ids.mapped("checkout")
        last_day = max(checkouts) if checkouts else self.create_date.date()
        return last_day + timedelta(
            days=self.pms_property_id.payment_card_retention_days
        )

    @api.model
    def _cron_purge(self):
        today = fields.Date.context_today(self)
        manual = self.sudo().search([("source_type", "=", "manual")])
        manual.filtered(lambda card: card._purge_date() < today).unlink()
        self.sudo().search(
            [
                ("source_type", "=", "channel"),
                ("available_until", "<", today),
                ("last4", "!=", False),
            ]
        ).write(self._no_masked_card())
