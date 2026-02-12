import base64
from typing import Annotated

from fastapi import File, HTTPException, UploadFile

from odoo import api, models

from odoo.addons.pms_fastapi.dependencies import AuthenticatedEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.user import User, UserUpdate


@pms_api_router.get("/user", response_model=User, tags=["user"])
async def get_user_info(env: AuthenticatedEnv) -> User:
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
    env: AuthenticatedEnv,
    userData: UserUpdate,
) -> User:
    """
    Update current user information.
    """
    helper = env["pms_api.user_router.helper"].new()
    helper.update_user(userData, env.user.id)
    return User.from_res_users(env.user)


@pms_api_router.put("/user/image", response_model=User, tags=["user"])
async def update_user_image(
    env: AuthenticatedEnv,
    image: Annotated[UploadFile, File(description="User image")],
):
    contents = await image.read()
    if not contents:
        raise HTTPException(
            status_code=400,
            detail="Empty image file uploaded. Use DELETE to remove image.",
        )
    helper = env["pms_api.user_router.helper"].new()
    helper.update_user_image(env.user.id, base64.b64encode(contents))
    return User.from_res_users(env.user)


@pms_api_router.delete("/user/image", response_model=User, tags=["user"])
async def delete_user_image(
    env: AuthenticatedEnv,
):
    helper = env["pms_api.user_router.helper"].new()
    helper.update_user_image(env.user.id, False)
    return User.from_res_users(env.user)


@pms_api_router.get("/user/extra-features", response_model=list[str], tags=["user"])
async def user_extra_features(
    env: AuthenticatedEnv,
) -> list[str]:
    return env["pms_api.user_router.helper"].extra_features()


class PmsApiUserRouterHelper(models.AbstractModel):
    _name = "pms_api.user_router.helper"
    _description = "Pms api contact Service Helper"

    def _prepare_write_res_users_vals(
        self,
        data: UserUpdate,
    ):
        return data.to_res_users()

    def update_user(self, data: UserUpdate, user_id: int):
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

    def update_user_image(self, user_id: int, image_data: bytes):
        user = self.env["res.users"].sudo().browse(user_id)
        user.partner_id.write({"image_1024": image_data})
        return user

    @api.model
    def extra_features(self):
        return []
