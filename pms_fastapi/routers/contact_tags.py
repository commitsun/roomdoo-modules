from typing import Annotated

from fastapi import Depends

from odoo.api import Environment

from odoo.addons.fastapi_auth_jwt.dependencies import AuthJwtOdooEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.contact_tag import ContactTagId


@pms_api_router.get(
    "/contact-tags", response_model=list[ContactTagId], tags=["db_info"]
)
async def get_contact_tags(
    env: Annotated[Environment, Depends(AuthJwtOdooEnv(validator_name="api_pms"))],
) -> list[ContactTagId]:
    """
    Get contact tags configured in the instance.
    """
    contact_tags = env["res.partner.category"].sudo().search([])
    return [
        ContactTagId.from_res_partner_category(contact_tag)
        for contact_tag in contact_tags
    ]
