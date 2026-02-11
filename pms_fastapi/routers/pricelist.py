from typing import Annotated

from fastapi import Depends

from odoo.api import Environment

from odoo.addons.fastapi_auth_jwt.dependencies import AuthJwtOdooEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.pricelist import PricelistId


@pms_api_router.get("/pricelists", response_model=list[PricelistId], tags=["pricelist"])
async def get_pricelists(
    env: Annotated[Environment, Depends(AuthJwtOdooEnv(validator_name="api_pms"))]
) -> list[PricelistId]:
    """
    Get pricelists configured in the instance.
    """
    pricelists = env["product.pricelist"].sudo().search([])
    return [PricelistId.from_product_pricelist(pricelist) for pricelist in pricelists]
