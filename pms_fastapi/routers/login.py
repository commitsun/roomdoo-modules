import re
import secrets
from datetime import timedelta

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse

from odoo import _, fields, models
from odoo.exceptions import AccessDenied

from odoo.addons.pms_fastapi.dependencies import PublicEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.pms_login import PmsLoginInput, PmsMfaInput

MFA_COOKIE = "mfa"
MFA_MAX_AGE = 5 * 60
MFA_MAX_ATTEMPTS = 5
DEVICE_COOKIE = "pms_td"
DEVICE_SCOPE = "api"
DEVICE_MAX_AGE = 90 * 86400
# The only second factor this API knows how to ask for.
SUPPORTED_MFA_TYPE = "totp"

UNAUTHORIZED_RESPONSE = {
    "description": "Unauthorized",
    "content": {
        "application/problem+json": {
            "example": {
                "type": "/errors/invalid-credentials",
                "title": "Invalid credentials",
                "status": 401,
                "detail": "Wrong user or password.",
            }
        }
    },
}


@pms_api_router.post(
    "/login",
    status_code=204,
    responses={401: UNAUTHORIZED_RESPONSE, 204: {"model": None}},
    tags=["login"],
)
async def login(user: PmsLoginInput, request: Request, env: PublicEnv):
    """
    Starts a session.

    When the account is protected by a second factor the answer is
    `/errors/mfa-required` and the code has to be sent to `/login/mfa`, unless
    this device was remembered on a previous login.
    """
    endpoint = env["pms.fastapi.login.endpoint"]
    user_record = env["res.users"].sudo().search([("login", "=", user.username)])
    if not user_record:
        return endpoint._invalid_credentials_response()
    try:
        # A person is waiting on the other side, so a password is the expected
        # credential here; the second factor is asked for below.
        user_record.with_user(user_record)._check_credentials(
            user.password.get_secret_value(), {"interactive": True}
        )
    except AccessDenied:
        return endpoint._invalid_credentials_response()
    return endpoint._authenticated_response(
        user_record, request.cookies.get(DEVICE_COOKIE)
    )


@pms_api_router.post(
    "/login/mfa",
    status_code=204,
    responses={401: UNAUTHORIZED_RESPONSE, 204: {"model": None}},
    tags=["login"],
)
async def login_mfa(verification: PmsMfaInput, request: Request, env: PublicEnv):
    """
    Finishes a login that was answered with `/errors/mfa-required`.

    A wrong code can be tried again. Once the answer is
    `/errors/mfa-too-many-attempts` the whole login has to start over.
    """
    return env["pms.fastapi.login.endpoint"]._verification_response(
        request.cookies.get(MFA_COOKIE),
        verification.code,
        verification.remember,
        request.headers.get("user-agent"),
    )


class PmsFastapiLoginEndpoint(models.AbstractModel):
    _name = "pms.fastapi.login.endpoint"
    _description = "login endpoint helper"

    def _invalid_credentials_response(self):
        return self._problem_response(
            "/errors/invalid-credentials",
            _("Invalid credentials"),
            _("Wrong user or password."),
        )

    def _problem_response(self, type_, title, detail):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            media_type="application/problem+json",
            content={
                "type": type_,
                "title": title,
                "status": status.HTTP_401_UNAUTHORIZED,
                "detail": detail,
            },
        )

    def _authenticated_response(self, user_record, device_token):
        """Either a session, or the demand for a second factor."""
        mfa_type = user_record._mfa_type()
        if not mfa_type:
            return self._get_login_response_with_cookies(user_record)
        if device_token and self._is_remembered_device(user_record, device_token):
            return self._get_login_response_with_cookies(user_record)
        if mfa_type != SUPPORTED_MFA_TYPE:
            # Letting an account through because its second factor is one this
            # API cannot ask for would turn the protection into a hole.
            return self._problem_response(
                "/errors/mfa-required",
                _("Second factor required"),
                _("This account cannot be used from here."),
            )
        return self._demand_verification_response(user_record)

    def _demand_verification_response(self, user_record):
        token = secrets.token_urlsafe(32)
        self.env["pms.fastapi.mfa.challenge"].sudo().create(
            {
                "user_id": user_record.id,
                "token": token,
                "expire": fields.Datetime.now() + timedelta(seconds=MFA_MAX_AGE),
            }
        )
        response = self._problem_response(
            "/errors/mfa-required",
            _("Second factor required"),
            _("Send the code shown by your authentication app."),
        )
        self._set_cookie(response, MFA_COOKIE, token, MFA_MAX_AGE)
        return response

    def _verification_response(self, token, code, remember, user_agent=None):
        challenge = self._open_challenge(token)
        if not challenge:
            return self._start_over_response()
        user_record = challenge.user_id
        try:
            with user_record._assert_can_auth(user=user_record.id):
                user_record._totp_check(int(re.sub(r"\s", "", code)))
        except (AccessDenied, ValueError):
            # Returned and not raised: the attempt has to survive the answer,
            # or a wrong code would cost the caller nothing.
            challenge.attempts += 1
            if challenge.attempts >= MFA_MAX_ATTEMPTS:
                challenge.unlink()
                return self._start_over_response()
            return self._problem_response(
                "/errors/mfa-invalid-code",
                _("Invalid code"),
                _("The code is not valid."),
            )
        challenge.unlink()
        response = self._get_login_response_with_cookies(user_record)
        if remember:
            self._remember_device(user_record, response, user_agent)
        self._set_cookie(response, MFA_COOKIE, "", 0)
        return response

    def _start_over_response(self):
        return self._problem_response(
            "/errors/mfa-too-many-attempts",
            _("Verification no longer possible"),
            _("Log in again."),
        )

    def _open_challenge(self, token):
        Challenge = self.env["pms.fastapi.mfa.challenge"].sudo()
        if not token:
            return Challenge
        return Challenge.search(
            [("token", "=", token), ("expire", ">", fields.Datetime.now())], limit=1
        )

    def _is_remembered_device(self, user_record, token):
        return (
            self.env["auth_totp.device"]
            .sudo()
            ._check_credentials_for_uid(
                scope=DEVICE_SCOPE, key=token, uid=user_record.id
            )
        )

    def _remember_device(self, user_record, response, user_agent):
        # Generated as the user: the key belongs to whoever is logging in, not
        # to the account running the API.
        key = (
            self.env["auth_totp.device"]
            .with_user(user_record)
            .sudo()
            ._generate(DEVICE_SCOPE, self._device_name(user_agent))
        )
        self._set_cookie(response, DEVICE_COOKIE, key, DEVICE_MAX_AGE)

    def _device_name(self, user_agent):
        """What the user will read in the trusted devices list."""
        return (user_agent or _("Unknown device"))[:128]

    def _set_cookie(self, response, key, value, max_age):
        validator = (
            self.env["auth.jwt.validator"].sudo()._get_validator_by_name("api_pms")
        )
        response.set_cookie(
            key=key,
            value=value,
            max_age=max_age,
            path=validator.cookie_path or "/",
            secure=validator.cookie_secure,
            httponly=True,
            # The client always lives on a sibling host of this one, so the
            # cookie is first party and does not need the cross-site opt-in.
            samesite="Lax",
        )

    def _get_login_response_with_cookies(self, user_record):
        validator = (
            self.env["auth.jwt.validator"].sudo()._get_validator_by_name("api_pms")
        )
        assert len(validator) == 1
        payload = {
            "username": user_record.login,
        }
        token = validator._encode(
            payload,
            secret=validator.secret_key,
            expire=validator.cookie_max_age,
        )
        response = Response(status_code=status.HTTP_204_NO_CONTENT)
        self._set_cookie(
            response, validator.cookie_name, token, validator.cookie_max_age
        )
        return response
