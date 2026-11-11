# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import base64

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .. import card_crypto

MIN_PASSWORD_LENGTH = 8


def _b64(value):
    return base64.b64encode(value).decode("ascii")


def _unb64(value):
    return base64.b64decode(value.encode("ascii"))


class PmsPaymentCardVault(models.Model):
    """The key pair of a property and its card password.

    See ``card_crypto`` for how the password protects the cards. A property
    has a card password exactly when it has a vault.
    """

    _name = "pms.payment.card.vault"
    _description = "Payment card vault"

    pms_property_id = fields.Many2one(
        comodel_name="pms.property",
        required=True,
        ondelete="cascade",
        index=True,
    )
    public_key = fields.Char(required=True)
    private_key = fields.Char(required=True)
    kdf_salt = fields.Char(required=True)
    kdf_n = fields.Integer(required=True)
    kdf_r = fields.Integer(required=True)
    kdf_p = fields.Integer(required=True)

    _sql_constraints = [
        (
            "pms_property_unique",
            "unique(pms_property_id)",
            "A property has a single card password.",
        ),
    ]

    @api.model
    def _get_for_property(self, pms_property):
        return self.sudo().search([("pms_property_id", "=", pms_property.id)], limit=1)

    @api.model
    def _check_new_password(self, password):
        if len(password or "") < MIN_PASSWORD_LENGTH:
            raise UserError(
                _(
                    "The card password must have at least %(length)s characters.",
                    length=MIN_PASSWORD_LENGTH,
                )
            )

    @api.model
    def _vault_values(self, vault):
        return {
            "public_key": _b64(vault["public_key"]),
            "private_key": _b64(vault["private_key"]),
            "kdf_salt": _b64(vault["salt"]),
            "kdf_n": vault["n"],
            "kdf_r": vault["r"],
            "kdf_p": vault["p"],
        }

    def _crypto_vault(self):
        self.ensure_one()
        return {
            "public_key": _unb64(self.public_key),
            "private_key": _unb64(self.private_key),
            "salt": _unb64(self.kdf_salt),
            "n": self.kdf_n,
            "r": self.kdf_r,
            "p": self.kdf_p,
        }

    @api.model
    def _create_for_property(self, pms_property, password):
        if self._get_for_property(pms_property):
            raise UserError(_("This property already has a card password."))
        self._check_new_password(password)
        values = self._vault_values(card_crypto.create_vault(password))
        return self.sudo().create(dict(values, pms_property_id=pms_property.id))

    def _change_password(self, password, new_password):
        self.ensure_one()
        self._check_new_password(new_password)
        try:
            vault = card_crypto.rewrap_vault(
                self._crypto_vault(), password, new_password
            )
        except card_crypto.InvalidPassword:
            raise UserError(_("The current card password is not correct.")) from None
        self.sudo().write(self._vault_values(vault))

    def _reset_password(self, new_password):
        """Replace the key pair: whatever the old one sealed is lost."""
        self.ensure_one()
        self._check_new_password(new_password)
        self._before_reset()
        self.sudo().write(self._vault_values(card_crypto.create_vault(new_password)))

    def _before_reset(self):
        """Drop what only the old key pair opens. Extended by the bridges."""
        self.env["pms.folio.payment.card"].sudo().search(
            [
                ("pms_property_id", "=", self.pms_property_id.id),
                ("source_type", "=", "manual"),
            ]
        ).unlink()

    def _unlock(self, password):
        """Return the private key, or raise ``card_crypto.InvalidPassword``."""
        self.ensure_one()
        return card_crypto.unlock_vault(self._crypto_vault(), password)

    def _associated_data(self, record):
        """Bind a sealed value to its record and to this database.

        A copy of the database gets a new uuid, so its sealed values stop
        opening even with the right password.
        """
        uuid = self.env["ir.config_parameter"].sudo().get_param("database.uuid")
        return f"{uuid}:{record._name}:{record.id}".encode()

    def _seal(self, record, plaintext):
        self.ensure_one()
        sealed = card_crypto.seal(
            _unb64(self.public_key), plaintext, self._associated_data(record)
        )
        return _b64(sealed)

    def _open(self, private_key, record, sealed):
        """Return the plaintext, or raise ``card_crypto.SealedValueError``."""
        self.ensure_one()
        return card_crypto.open_sealed(
            private_key, _unb64(sealed), self._associated_data(record)
        )
