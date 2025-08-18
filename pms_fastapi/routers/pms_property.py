from typing import Annotated

from fastapi import Depends

from odoo.api import Environment

from odoo.addons.fastapi_auth_jwt.dependencies import AuthJwtOdooEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.pms_property import PropertySummary


@pms_api_router.get(
    "/properties",
    status_code=200,
    responses={
        200: {"model": None},
    },
    response_model=list[PropertySummary],
    tags=["property"],
)
async def get_property_links(
    env: Annotated[Environment, Depends(AuthJwtOdooEnv(validator_name="api_pms"))],
) -> list[PropertySummary]:
    """
    Returns a list of available properties
    """
    properties = env["pms.property"].sudo().search([])
    return [
        PropertySummary.from_pms_property(pms_property) for pms_property in properties
    ]
