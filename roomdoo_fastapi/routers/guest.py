from odoo.addons.pms_fastapi.dependencies import AuthenticatedEnv
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
    env: AuthenticatedEnv,
) -> int:
    # We need to intialize GuestSearch to have the default pmsProperty value.
    return env["pms_api_guest.guest_router.helper"].new().count(GuestSearch())
