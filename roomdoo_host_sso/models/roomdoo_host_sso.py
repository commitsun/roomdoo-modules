import logging

from jwt import PyJWKClientError

from odoo import _, api, models
from odoo.exceptions import AccessDenied

from odoo.addons.auth_jwt.exceptions import UnauthorizedInvalidToken
from odoo.addons.roomdoo_host_link.models.host_link import PARAM_INSTANCE, PARAM_URL

_logger = logging.getLogger(__name__)

VALIDATOR = "roomdoo_host"
JWKS_PATH = "/roomdoo/sso/jwks.json"
DOORS = ("back", "front")


class RoomdooHostSso(models.AbstractModel):
    """Entries of the Roomdoo team, let in by a token host signs.

    Host signs with a key only host holds and publishes the public half, so
    this instance trusts a token without sharing any secret for it. Each
    token is for this instance and one door, lives a minute and opens once.
    """

    _name = "roomdoo.host.sso"
    _description = "Entry from Roomdoo host"

    @api.model
    def _validator(self):
        """The validator of host's tokens, kept in line with the host link."""
        icp = self.env["ir.config_parameter"].sudo()
        url = (icp.get_param(PARAM_URL) or "").rstrip("/")
        code = icp.get_param(PARAM_INSTANCE) or ""
        if not (url and code):
            raise AccessDenied(_("This instance is not linked to host."))
        wanted = {
            "signature_type": "public_key",
            "public_key_jwk_uri": url + JWKS_PATH,
            "public_key_algorithm": "RS256",
            "audience": ",".join(f"{code}-{door}" for door in DOORS),
            "issuer": url,
            "user_id_strategy": "static",
        }
        validators = self.env["auth.jwt.validator"].sudo()
        validator = validators.search([("name", "=", VALIDATOR)], limit=1)
        if not validator:
            return validators.create(dict(wanted, name=VALIDATOR))
        changed = {
            key: value for key, value in wanted.items() if validator[key] != value
        }
        if changed:
            validator.write(changed)
        return validator

    @api.model
    def _enter(self, token, door):
        """The user that host's token lets in through this door."""
        validator = self._validator()
        try:
            claims = validator._decode(token or "")
        except (UnauthorizedInvalidToken, PyJWKClientError) as exc:
            _logger.info("Refused an entry from host: %s", exc)
            raise AccessDenied(_("This entry link is not valid.")) from exc
        code = self.env["ir.config_parameter"].sudo().get_param(PARAM_INSTANCE)
        if claims.get("aud") != f"{code}-{door}" or not claims.get("jti"):
            raise AccessDenied(_("This entry link is for another door."))
        self.env["roomdoo.host.sso.token"]._spend(claims["jti"], claims["exp"])
        return self.env["res.users"]._host_sso_provision(claims)
