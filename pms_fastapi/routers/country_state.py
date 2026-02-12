from odoo.addons.pms_fastapi.dependencies import PublicEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.country_state import CountryStateSummary


@pms_api_router.get(
    "/country-states", response_model=list[CountryStateSummary], tags=["db_info"]
)
async def get_country_states(
    env: PublicEnv,
    country: int | None = None,
) -> list[CountryStateSummary]:
    """
    Get country states configured in the instance.
    """
    country_state_search = []
    if country:
        country_state_search.append(("country_id", "=", country))
    country_states = env["res.country.state"].sudo().search(country_state_search)
    return [
        CountryStateSummary.from_res_country_state(country_state)
        for country_state in country_states
    ]
