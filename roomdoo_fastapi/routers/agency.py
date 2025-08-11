from typing import Annotated

from fastapi import Depends

from odoo import models
from odoo.api import Environment

from odoo.addons.extendable_fastapi.schemas import PagedCollection
from odoo.addons.fastapi.dependencies import (
    paging,
)
from odoo.addons.fastapi.schemas import Paging
from odoo.addons.fastapi_auth_jwt.dependencies import AuthJwtOdooEnv
from odoo.addons.pms_fastapi.dependencies import create_order_dependency
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.roomdoo_fastapi.schemas.agency import (
    AGENCY_ORDER_MAPPING,
    AgencyOrderField,
    AgencySearch,
    AgencySummary,
)

ContactOrderDependency = create_order_dependency(
    AgencyOrderField, AGENCY_ORDER_MAPPING, ["name"]
)


@pms_api_router.get(
    "/agencies", response_model=PagedCollection[AgencySummary], tags=["contact"]
)
async def list_agencies(
    env: Annotated[Environment, Depends(AuthJwtOdooEnv(validator_name="api_pms"))],
    filters: Annotated[AgencySearch, Depends()],
    paging: Annotated[Paging, Depends(paging)],
    orderBy: Annotated[str, Depends(ContactOrderDependency)],
) -> list[AgencySummary]:
    """Get the list of the agencies"""
    count, agencies = (
        env["pms_api_agency.agency_router.helper"]
        .new()
        ._search(paging, filters, orderBy)
    )

    return PagedCollection[AgencySummary](
        count=count,
        items=[AgencySummary.from_res_partner(agency) for agency in agencies],
    )


class PmsApiContactRouterHelper(models.AbstractModel):
    _name = "pms_api_agency.agency_router.helper"
    _inherit = "pms_api_contact.contact_router.helper"
    _description = "Pms api agency Service Helper"

    def _get_domain_adapter(self):
        return [("is_agency", "=", True)]
