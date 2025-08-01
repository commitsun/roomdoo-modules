from typing import Annotated

from fastapi import Depends

from odoo.api import Environment

from odoo.addons.fastapi_auth_jwt.dependencies import AuthJwtOdooEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.user import User


@pms_api_router.get("/user", response_model=User, tags=["user"])
async def get_user_info(
    env: Annotated[Environment, Depends(AuthJwtOdooEnv(validator_name="api_pms"))]
) -> User:
    """
    Get current user basic information.
    """
    user = env.user
    return User.from_res_users(user)
