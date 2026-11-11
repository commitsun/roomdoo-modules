# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""Errors of the payment card API, one per answer the front tells apart.

They are not Odoo exceptions on purpose. The API answers them instead of
raising them, so the request still commits: a wrong password has to leave its
failed attempt and its access log behind, and a raised error would roll both
back.
"""


class PaymentCardError(Exception):
    status = 400
    code = "error"

    def __init__(self, description):
        super().__init__(description)
        self.description = description

    def to_json(self):
        return {"code": self.code, "description": self.description}


class FolioNotFound(PaymentCardError):
    status = 404
    code = "folio_not_found"


class CardForbidden(PaymentCardError):
    status = 403
    code = "forbidden"


class CardPasswordNotConfigured(PaymentCardError):
    status = 403
    code = "card_password_not_configured"


class CardNotAvailable(PaymentCardError):
    status = 404
    code = "card_not_available"


class CardAlreadyExists(PaymentCardError):
    status = 409
    code = "card_already_exists"


class InvalidCardPassword(PaymentCardError):
    status = 422
    code = "invalid_card_password"


class CardValidationError(PaymentCardError):
    status = 422
    code = "validation_error"


class CardPasswordLocked(PaymentCardError):
    status = 429
    code = "card_password_locked"


class ChannelManagerUnavailable(PaymentCardError):
    status = 502
    code = "channel_manager_unavailable"


RESULTS = [
    ("success", "Success"),
    (FolioNotFound.code, "Folio not found"),
    (CardForbidden.code, "Forbidden"),
    (CardPasswordNotConfigured.code, "Card password not configured"),
    (CardNotAvailable.code, "Card not available"),
    (CardAlreadyExists.code, "Card already exists"),
    (InvalidCardPassword.code, "Invalid card password"),
    (CardValidationError.code, "Validation error"),
    (CardPasswordLocked.code, "Locked"),
    (ChannelManagerUnavailable.code, "Channel manager unavailable"),
]
