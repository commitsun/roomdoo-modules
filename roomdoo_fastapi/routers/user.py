from datetime import datetime, timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, Response, status

from odoo.api import Environment
from odoo.exceptions import AccessDenied

from odoo.addons.fastapi.dependencies import odoo_env
from odoo.addons.fastapi_auth_jwt.dependencies import AuthJwtOdooEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.roomdoo_fastapi.schemas.user import (
    AvailabilityRuleField,
    ChangePasswordInput,
    ResetPasswordInput,
    UserEmailInput,
)


@pms_api_router.get(
    "/user/availability-rule-fields",
    response_model=list[AvailabilityRuleField],
    tags=["user"],
)
async def get_availability_rule_fields(
    env: Annotated[Environment, Depends(AuthJwtOdooEnv(validator_name="api_pms"))]
) -> list[AvailabilityRuleField]:
    """
    Get user availability rules fields for user interface.
    """
    user = env.user
    return [
        AvailabilityRuleField(name=rule.name)
        for rule in user.availability_rule_field_ids
    ]


@pms_api_router.patch(
    "/user/change-password",
    responses={
        401: {
            "description": "Unauthorized",
            "content": {"application/json": {"example": {"detail": "wrong user/pass"}}},
        },
        204: {"model": None},
    },
    tags=["user"],
)
async def change_password(
    password_item: ChangePasswordInput,
    env: Annotated[Environment, Depends(AuthJwtOdooEnv(validator_name="api_pms"))],
):
    user = env.user
    old_password = password_item.oldPassword.get_secret_value()
    new_password = password_item.newPassword.get_secret_value()
    try:
        user._check_credentials(old_password, {"interactive": False})
    except AccessDenied as e:
        raise HTTPException(status_code=401, detail="Wrong user/password") from e

    user.change_password(old_password, new_password)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@pms_api_router.post(
    "/send-mail-reset-password",
    status_code=204,
    responses={
        204: {"model": None},
    },
    tags=["user"],
)
async def send_mail_reset_password(
    userEmail: UserEmailInput, env: Annotated[Environment, Depends(odoo_env)]
):
    user = env["res.users"].sudo().search([("email", "=", userEmail.email)])
    if user:
        template_id = env.ref("pms_api_rest.pms_reset_password_email").id
        template = env["mail.template"].sudo().browse(template_id)
        expiration_datetime = datetime.now() + timedelta(minutes=15)
        user.partner_id.sudo().signup_prepare(expiration=expiration_datetime)
        app_url = env["ir.config_parameter"].sudo().get_param("roomdoo_app_url")
        template.with_context(app_url=app_url).send_mail(user.id, force_send=True)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@pms_api_router.patch(
    "/reset-password",
    status_code=204,
    responses={
        204: {"model": None},
    },
    tags=["user"],
)
async def reset_password(
    reset_pass_input: ResetPasswordInput, env: Annotated[Environment, Depends(odoo_env)]
):
    password = reset_pass_input.newPassword.get_secret_value()
    reset_token = reset_pass_input.resetToken.get_secret_value()
    values = {
        "password": password,
    }
    env["res.users"].sudo().signup(values, reset_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
