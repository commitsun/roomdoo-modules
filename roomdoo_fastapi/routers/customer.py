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
from odoo.addons.roomdoo_fastapi.schemas.customer import (
    CUSTOMER_ORDER_MAPPING,
    CustomerOrderField,
    CustomerSearch,
    CustomerSummary,
)

ContactOrderDependency = create_order_dependency(
    CustomerOrderField, CUSTOMER_ORDER_MAPPING, ["name"]
)


@pms_api_router.get(
    "/customers", response_model=PagedCollection[CustomerSummary], tags=["contact"]
)
async def list_customers(
    env: Annotated[Environment, Depends(AuthJwtOdooEnv(validator_name="api_pms"))],
    filters: Annotated[CustomerSearch, Depends()],
    paging: Annotated[Paging, Depends(paging)],
    orderBy: Annotated[str, Depends(ContactOrderDependency)],
) -> list[CustomerSummary]:
    """Get the list of the customers"""
    count, customers = (
        env["pms_api_customer.customer_router.helper"]
        .new()
        ._search(paging, filters, orderBy)
    )

    return PagedCollection[CustomerSummary](
        count=count,
        items=[CustomerSummary.from_res_partner(customer) for customer in customers],
    )


class PmsApiContactRouterHelper(models.AbstractModel):
    _name = "pms_api_customer.customer_router.helper"
    _inherit = "pms_api_contact.contact_router.helper"
    _description = "Pms api customer Service Helper"

    def _get_domain_adapter(self):
        res = super()._get_domain_adapter()
        if res is None:
            res = []
        res = expression.AND([res, [("customer_rank", ">", 0)]])
        return res
