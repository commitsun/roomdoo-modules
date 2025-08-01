from typing import Annotated

from fastapi import Depends

from odoo.api import Environment

from odoo.addons.fastapi.dependencies import odoo_env
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.country import CountryId


@pms_api_router.get("/countries", response_model=list[CountryId], tags=["db_info"])
async def get_server_countries(
    env: Annotated[Environment, Depends(odoo_env)]
) -> list[CountryId]:
    """
    Get countries configured in the instance.
    """
    countries = env["res.country"].sudo().search([])
    return [CountryId.from_res_country(country) for country in countries]
