from typing import Annotated

from fastapi import Depends, HTTPException

from odoo import models
from odoo.api import Environment

from odoo.addons.fastapi_auth_jwt.dependencies import AuthJwtOdooEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.user import User, UserUpdate


@pms_api_router.get("/user", response_model=User, tags=["user"])
async def get_user_info(
    env: Annotated[Environment, Depends(AuthJwtOdooEnv(validator_name="api_pms"))]
) -> User:
    """
    Get current user basic information.
    """
    user = env.user
    return User.from_res_users(user)


@pms_api_router.patch(
    "/user",
    response_model=User,
    tags=["user"],
)
async def update_user(
    env: Annotated[Environment, Depends(AuthJwtOdooEnv(validator_name="api_pms"))],
    userData: UserUpdate,
) -> User:
    """
    Update current user information.
    """
    helper = env["pms_api_contact.user_router.helper"].new()
    helper.update_contact(userData, env.user.id)
    return User.from_res_users(env.user)


class PmsApiContactRouterHelper(models.AbstractModel):
    _name = "pms_api_contact.user_router.helper"
    _description = "Pms api contact Service Helper"

    def _prepare_write_res_users_vals(
        self,
        data: UserUpdate,
    ):
        return data.to_res_users()

    def update_contact(self, data: UserUpdate, user_id: int):
        vals = self._prepare_write_res_users_vals(data)
        if vals.get("lang"):
            available_langs = [x[0] for x in self.env["res.lang"].get_installed()]
            if vals["lang"] not in available_langs:
                raise HTTPException(
                    status_code=400,
                    detail=f"Language '{vals['lang']}' is not available."
                    f" Choose from {available_langs}",
                )
        return self.env["res.users"].sudo().browse(user_id).write(vals)
