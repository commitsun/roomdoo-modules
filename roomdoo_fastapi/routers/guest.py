from typing import Annotated

from fastapi import Depends

from odoo.api import Environment

from odoo.addons.fastapi_auth_jwt.dependencies import AuthJwtOdooEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.guest import (
    GuestSearch,
)


@pms_api_router.get(
    "/guests-count",
    response_model=int,
    tags=["contact"],
)
async def count_guests(
    env: Annotated[Environment, Depends(AuthJwtOdooEnv(validator_name="api_pms"))],
) -> int:
    # We need to intialize GuestSearch to have the default pmsProperty value.
    return env["pms_api_guest.guest_router.helper"].new().count(GuestSearch())
