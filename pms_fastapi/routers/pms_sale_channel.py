from typing import Annotated

from fastapi import Depends

from odoo.api import Environment

from odoo.addons.extendable_fastapi.schemas import PagedCollection
from odoo.addons.fastapi_auth_jwt.dependencies import AuthJwtOdooEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.pms_sale_channel import SaleChannelSummary


@pms_api_router.get(
    "/sale-channels",
    response_model=PagedCollection[SaleChannelSummary],
    tags=["contact"],
)
async def get_sale_channels(
    env: Annotated[Environment, Depends(AuthJwtOdooEnv(validator_name="api_pms"))],
) -> list[SaleChannelSummary]:
    """
    Get a list of sale channels.
    """
    channels = env["pms.sale.channel"].search([])
    return [SaleChannelSummary.from_pms_sale_channel(channel) for channel in channels]
