from odoo.addons.pms_fastapi.dependencies import AuthenticatedEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.pms_sale_channel import SaleChannelSummary


@pms_api_router.get(
    "/sale-channels",
    response_model=list[SaleChannelSummary],
    tags=["contact"],
)
async def get_sale_channels(
    env: AuthenticatedEnv,
) -> list[SaleChannelSummary]:
    """
    Get a list of sale channels.
    """
    channels = env["pms.sale.channel"].sudo().search([])
    return [SaleChannelSummary.from_pms_sale_channel(channel) for channel in channels]
