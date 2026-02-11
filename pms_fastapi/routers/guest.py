from typing import Annotated

from fastapi import Depends

from odoo import models
from odoo.api import Environment
from odoo.osv import expression

from odoo.addons.fastapi.dependencies import (
    paging,
)
from odoo.addons.fastapi.schemas import PagedCollection, Paging
from odoo.addons.fastapi_auth_jwt.dependencies import AuthJwtOdooEnv
from odoo.addons.pms_fastapi.dependencies import create_order_dependency
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.guest import (
    GUEST_ORDER_MAPPING,
    GuestOrderField,
    GuestSearch,
    GuestSummary,
)

ContactOrderDependency = create_order_dependency(
    GuestOrderField, GUEST_ORDER_MAPPING, ["name"]
)


@pms_api_router.get(
    "/guests", response_model=PagedCollection[GuestSummary], tags=["contact"]
)
async def list_guests(
    env: Annotated[Environment, Depends(AuthJwtOdooEnv(validator_name="api_pms"))],
    filters: Annotated[GuestSearch, Depends()],
    paging: Annotated[Paging, Depends(paging)],
    orderBy: Annotated[str, Depends(ContactOrderDependency)],
) -> list[GuestSummary]:
    """Get the list of the guests"""
    count, guests = (
        env["pms_api_guest.guest_router.helper"].new()._search(paging, filters, orderBy)
    )

    return PagedCollection[GuestSummary](
        count=count,
        items=[GuestSummary.from_res_partner(guest) for guest in guests],
    )


class PmsApiGuestRouterHelper(models.AbstractModel):
    _name = "pms_api_guest.guest_router.helper"
    _inherit = "pms_api_contact.contact_router.helper"
    _description = "Pms api guest Service Helper"

    def _get_domain_adapter(self):
        res = super()._get_domain_adapter()
        if res is None:
            res = []
        res = expression.AND([res, [("pms_checkin_partner_ids", "!=", False)]])
        return res
