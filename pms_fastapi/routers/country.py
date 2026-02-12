from odoo.addons.pms_fastapi.dependencies import PublicEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.country import CountrySummary


@pms_api_router.get("/countries", response_model=list[CountrySummary], tags=["db_info"])
async def get_server_countries(env: PublicEnv) -> list[CountrySummary]:
    """
    Get countries configured in the instance.
    """
    countries = env["res.country"].sudo().search([])
    return [CountrySummary.from_res_country(country) for country in countries]
