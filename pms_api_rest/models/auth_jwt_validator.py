from odoo import models

from odoo.addons.auth_jwt.exceptions import UnauthorizedInvalidToken

PMS_API_VALIDATOR = "api_pms"


class AuthJwtValidator(models.Model):
    _inherit = "auth.jwt.validator"

    def _get_and_check_uid(self, payload):
        """Turn away, on every request, the users the PMS APIs are not for.

        Both the REST services and the FastAPI routes resolve their user here,
        so this is the one place that also stops the tokens and cookies that
        were handed out before the user lost its access.
        """
        uid = super()._get_and_check_uid(payload)
        if self.name != PMS_API_VALIDATOR:
            return uid
        if not self.env["res.users"].sudo().browse(uid)._pms_api_check_access():
            raise UnauthorizedInvalidToken()
        return uid
