from odoo.addons.pms_fastapi.dependencies import AuthenticatedEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.contact_tag import ContactTagId


@pms_api_router.get(
    "/contact-tags", response_model=list[ContactTagId], tags=["db_info"]
)
async def get_contact_tags(
    env: AuthenticatedEnv,
) -> list[ContactTagId]:
    """
    Get contact tags configured in the instance.
    """
    contact_tags = env["res.partner.category"].sudo().search([])
    return [
        ContactTagId.from_res_partner_category(contact_tag)
        for contact_tag in contact_tags
    ]
