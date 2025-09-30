from typing import Annotated

from fastapi import Depends

from odoo.api import Environment

from odoo.addons.fastapi.dependencies import odoo_env
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.contact_tag import ContactTagId


@pms_api_router.get(
    "/contact-tags", response_model=list[ContactTagId], tags=["db_info"]
)
async def get_country_states(
    env: Annotated[Environment, Depends(odoo_env)],
) -> list[ContactTagId]:
    """
    Get country states configured in the instance.
    """
    contact_tags = env["res.partner.category"].sudo().search([])
    return [
        ContactTagId.from_res_partner_category(contact_tag)
        for contact_tag in contact_tags
    ]
