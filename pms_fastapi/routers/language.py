from odoo.addons.pms_fastapi.dependencies import PublicEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.language import Language


@pms_api_router.get("/languages", response_model=list[Language], tags=["db_info"])
async def get_server_languages(env: PublicEnv) -> list[Language]:
    """
    Get server information including languages.
    """
    languages = env["res.lang"].sudo().search([])
    return [Language.from_res_lang(lang) for lang in languages]
