from typing import Annotated

from fastapi import Depends

from odoo.api import Environment

from odoo.addons.fastapi_auth_jwt.dependencies import AuthJwtOdooEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.roomdoo_fastapi.schemas.zip_autocomplete import ZipSummary


@pms_api_router.get(
    "/zip-autocomplete", response_model=list[ZipSummary], tags=["db_info"]
)
async def zip_autocomplete(
    env: Annotated[Environment, Depends(AuthJwtOdooEnv(validator_name="api_pms"))],
    searchParam: str,
) -> list[ZipSummary]:
    """
    Get zip code autocomplete suggestions based on the search parameter.
    Only searches with 3 or more characters will return results.
    returns a list of max 20 results.
    """
    if len(searchParam) < 3:
        return []
    records = env["res.city.zip"].name_search(searchParam, limit=20)
    zip_autocomplete_list = []
    for record in records:
        zip_record = env["res.city.zip"].browse(record[0])
        zip_autocomplete_list.append(ZipSummary.from_res_city_zip(zip_record))
    return zip_autocomplete_list
