from typing import Annotated

from fastapi import Depends

from odoo.api import Environment

from odoo.addons.fastapi.dependencies import odoo_env
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.country_state import CountryStateId


@pms_api_router.get(
    "/countrie-states", response_model=list[CountryStateId], tags=["db_info"]
)
async def get_country_states(
    env: Annotated[Environment, Depends(odoo_env)]
) -> list[CountryStateId]:
    """
    Get country states configured in the instance.
    """
    country_states = env["res.country.state"].sudo().search([])
    return [
        CountryStateId.from_res_country_state(country_state)
        for country_state in country_states
    ]
