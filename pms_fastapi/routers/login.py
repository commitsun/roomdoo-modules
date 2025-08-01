from typing import Annotated

from fastapi import Depends, HTTPException, Response, status

from odoo import models
from odoo.api import Environment
from odoo.exceptions import AccessDenied

from odoo.addons.fastapi.dependencies import odoo_env
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.pms_login import PmsLoginInput


@pms_api_router.post(
    "/login",
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
async def login(user: PmsLoginInput, env: Annotated[Environment, Depends(odoo_env)]):
    """
    If the login is correct, sets the authorization cookies.
    """
    user_record = env["res.users"].sudo().search([("login", "=", user.username)])

    if not user_record:
        raise HTTPException(
            status_code=401,
            detail="wrong user/pass",
        )
    try:
        user_record.with_user(user_record)._check_credentials(
            user.password.get_secret_value(), None
        )
    except AccessDenied as e:
        raise HTTPException(
            status_code=401,
            detail="wrong user/pass",
        ) from e
    return env["pms.fastapi.login.endpoint"]._get_login_response_with_cookies(
        user_record
    )


class PmsFastapiLoginEndpoint(models.AbstractModel):
    _name = "pms.fastapi.login.endpoint"
    _description = "Demo Endpoint"

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
        response.set_cookie(
            key=validator.cookie_name,
            value=token,
            max_age=validator.cookie_max_age,
            path=validator.cookie_path or "/",
            secure=validator.cookie_secure,
            httponly=True,
            samesite="Strict",
        )
        return response
