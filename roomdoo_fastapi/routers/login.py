from fastapi import HTTPException, Request

from odoo import models
from odoo.exceptions import AccessDenied

from odoo.addons.pms_fastapi.dependencies import PublicEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router


@pms_api_router.post(
    "/refresh-token",
    status_code=204,
    responses={
        401: {
            "description": "Unauthorized",
            "content": {"application/json": {"example": {"detail": "wrong user/pass"}}},
        },
        204: {"model": None},
    },
    tags=["login"],
)
async def refresh(request: Request, env: PublicEnv):
    """
    Refresh auth tokens. Should be called after the expiration of the access token.
    If returns 401 HTTP code, you should login again.
    """
    validator = env["auth.jwt.validator"].sudo()._get_validator_by_name("api_pms")
    refresh_cookie = request.cookies.get(validator.refresh_cookie_name)
    if refresh_cookie:
        try:
            return env["pms.fastapi.login.endpoint"]._refresh_token(refresh_cookie)
        except AccessDenied as e:
            raise HTTPException(
                status_code=401,
                detail="Expired token",
            ) from e
    else:
        raise HTTPException(
            status_code=401,
            detail="wrong token",
        )


class PmsFastapiLoginEndpoint(models.AbstractModel):
    _inherit = "pms.fastapi.login.endpoint"

    def _get_login_response_with_cookies(self, user_record):
        validator = (
            self.env["auth.jwt.validator"].sudo()._get_validator_by_name("api_pms")
        )
        response = super()._get_login_response_with_cookies(user_record)
        payload = {}
        refresh_token = validator._encode(
            payload,
            expire=validator.refresh_cookie_max_age,
            secret=validator.refresh_token_secret,
        )
        user_record._add_refresh_token(refresh_token, validator.refresh_cookie_max_age)
        response.set_cookie(
            key=validator.refresh_cookie_name,
            value=refresh_token,
            max_age=validator.refresh_cookie_max_age,
            path=validator.refresh_token_path or "/",
            secure=validator.cookie_secure,
            httponly=True,
            samesite="None",
        )
        return response

    def _refresh_token(self, token):
        user = self.env["res.users"].get_user_by_refresh_token(token)
        user.invalidate_refresh_token(token)
        return self._get_login_response_with_cookies(user)
