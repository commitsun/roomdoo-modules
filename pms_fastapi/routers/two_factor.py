import base64
import os
import re
from datetime import timedelta
from urllib.parse import quote, urlencode, urlparse

from fastapi import Response, status
from fastapi.responses import JSONResponse

from odoo import _, fields, models
from odoo.exceptions import AccessDenied

from odoo.addons.auth_totp.models.totp import (
    ALGORITHM,
    DIGITS,
    TIMESTEP,
    TOTP_SECRET_SIZE,
)
from odoo.addons.pms_fastapi.dependencies import AuthenticatedEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.two_factor import (
    TwoFactorActivation,
    TwoFactorSetup,
)

SETUP_MAX_AGE = 15 * 60


@pms_api_router.get("/user/two-factor", response_model=TwoFactorSetup, tags=["user"])
async def start_two_factor(env: AuthenticatedEnv):
    """
    Offers a second factor to the account in session.

    The returned link is what an authentication app reads, usually from a QR
    code; the secret is the same thing in text, for typing it by hand. Neither
    protects the account until it is confirmed.
    """
    return env["pms.fastapi.two.factor.endpoint"]._offer(env.user)


@pms_api_router.post("/user/two-factor", status_code=204, tags=["user"])
async def activate_two_factor(activation: TwoFactorActivation, env: AuthenticatedEnv):
    """
    Turns on the second factor that was offered.

    The password is asked for again so that an unattended session cannot
    change how the account is protected.
    """
    return env["pms.fastapi.two.factor.endpoint"]._activate(
        env.user, activation.code, activation.password.get_secret_value()
    )


class PmsFastapiTwoFactorEndpoint(models.AbstractModel):
    _name = "pms.fastapi.two.factor.endpoint"
    _description = "two factor setup helper"

    def _offer(self, user):
        if user.totp_enabled:
            return self._already_protected_response()
        secret = base64.b32encode(os.urandom(TOTP_SECRET_SIZE // 8)).decode()
        self._store_offer(user, secret)
        return TwoFactorSetup(uri=self._uri(user, secret), secret=secret)

    def _store_offer(self, user, secret):
        Setup = self.env["pms.fastapi.mfa.setup"].sudo()
        values = {
            "secret": secret,
            "expire": fields.Datetime.now() + timedelta(seconds=SETUP_MAX_AGE),
        }
        pending = Setup.search([("user_id", "=", user.id)], limit=1)
        if pending:
            return pending.write(values)
        return Setup.create(dict(values, user_id=user.id))

    def _activate(self, user, code, password):
        if user.totp_enabled:
            return self._already_protected_response()
        pending = (
            self.env["pms.fastapi.mfa.setup"]
            .sudo()
            .search(
                [("user_id", "=", user.id), ("expire", ">", fields.Datetime.now())],
                limit=1,
            )
        )
        if not pending:
            return self._offer_required_response()
        try:
            user._check_credentials(password, {"interactive": True})
        except AccessDenied:
            return self._problem_response(
                status.HTTP_401_UNAUTHORIZED,
                "/errors/invalid-credentials",
                _("Invalid credentials"),
                _("Wrong password."),
            )
        try:
            accepted = user._totp_try_setting(
                pending.secret, int(re.sub(r"\s", "", code))
            )
        except ValueError:
            accepted = False
        if not accepted:
            return self._problem_response(
                status.HTTP_401_UNAUTHORIZED,
                "/errors/mfa-invalid-code",
                _("Invalid code"),
                _("The code is not valid."),
            )
        pending.unlink()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    def _uri(self, user, secret):
        """The link an authentication app expects, as its own spec defines it."""
        issuer = self._issuer()
        label = quote(f"{issuer}:{user.login}", safe=":")
        settings = urlencode(
            {
                "secret": secret,
                "issuer": issuer,
                "algorithm": ALGORITHM.upper(),
                "digits": DIGITS,
                "period": TIMESTEP,
            }
        )
        return f"otpauth://totp/{label}?{settings}"

    def _issuer(self):
        """What the user will read as the account name in their app.

        The address they reach the client at, so that they recognise it. The
        parameter can hold anything, including the placeholder it ships with,
        so whatever is not an address falls back to the company.
        """
        app_url = (
            self.env["ir.config_parameter"].sudo().get_param("roomdoo_app_url") or ""
        )
        # A colon would break the label apart where the app splits it.
        host = urlparse(app_url.strip()).netloc.split(":")[0]
        return host or self.env.company.display_name

    def _already_protected_response(self):
        return self._problem_response(
            status.HTTP_409_CONFLICT,
            "/errors/mfa-already-enabled",
            _("Already protected"),
            _("This account already asks for a code."),
        )

    def _offer_required_response(self):
        return self._problem_response(
            status.HTTP_409_CONFLICT,
            "/errors/mfa-setup-required",
            _("Nothing to confirm"),
            _("Start again to get a new code to scan."),
        )

    def _problem_response(self, status_code, type_, title, detail):
        return JSONResponse(
            status_code=status_code,
            media_type="application/problem+json",
            content={
                "type": type_,
                "title": title,
                "status": status_code,
                "detail": detail,
            },
        )
