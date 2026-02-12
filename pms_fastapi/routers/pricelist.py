from odoo.addons.pms_fastapi.dependencies import AuthenticatedEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.pricelist import PricelistId


@pms_api_router.get("/pricelists", response_model=list[PricelistId], tags=["pricelist"])
async def get_pricelists(
    env: AuthenticatedEnv,
) -> list[PricelistId]:
    """
    Get pricelists configured in the instance.
    """
    pricelists = env["product.pricelist"].sudo().search([])
    return [PricelistId.from_product_pricelist(pricelist) for pricelist in pricelists]
