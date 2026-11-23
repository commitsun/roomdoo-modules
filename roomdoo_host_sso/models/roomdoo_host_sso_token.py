from datetime import datetime, timedelta, timezone

from odoo import _, api, fields, models
from odoo.exceptions import AccessDenied


class RoomdooHostSsoToken(models.Model):
    """Tokens already used: each one opens a door once."""

    _name = "roomdoo.host.sso.token"
    _description = "Spent entry token from host"

    jti = fields.Char("Token ID", required=True, readonly=True, index=True)
    expires_at = fields.Datetime(required=True, readonly=True)

    _sql_constraints = [("jti_unique", "unique(jti)", "A token is only spent once.")]

    @api.model
    def _spend(self, jti, exp):
        tokens = self.sudo()
        if tokens.search_count([("jti", "=", jti)]):
            raise AccessDenied(_("This entry link has already been used."))
        expires = datetime.fromtimestamp(exp, tz=timezone.utc).replace(tzinfo=None)
        tokens.create({"jti": jti, "expires_at": expires})

    @api.autovacuum
    def _gc_spent_tokens(self):
        """Once expired a token is refused anyway: no need to remember it."""
        limit = fields.Datetime.now() - timedelta(hours=1)
        self.sudo().search([("expires_at", "<", limit)]).unlink()
