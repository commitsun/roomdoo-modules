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
from odoo.addons.pms_fastapi.schemas.supplier import (
    SUPPLIER_ORDER_MAPPING,
    SupplierOrderField,
    SupplierSearch,
    SupplierSummary,
)

ContactOrderDependency = create_order_dependency(
    SupplierOrderField, SUPPLIER_ORDER_MAPPING, ["name"]
)


@pms_api_router.get(
    "/suppliers", response_model=PagedCollection[SupplierSummary], tags=["contact"]
)
async def list_suppliers(
    env: Annotated[Environment, Depends(AuthJwtOdooEnv(validator_name="api_pms"))],
    filters: Annotated[SupplierSearch, Depends()],
    paging: Annotated[Paging, Depends(paging)],
    orderBy: Annotated[str, Depends(ContactOrderDependency)],
) -> list[SupplierSummary]:
    """Get the list of the suppliers"""
    count, suppliers = (
        env["pms_api_supplier.supplier_router.helper"]
        .new()
        ._search(paging, filters, orderBy)
    )

    return PagedCollection[SupplierSummary](
        count=count,
        items=[SupplierSummary.from_res_partner(supplier) for supplier in suppliers],
    )


class PmsApiContactRouterHelper(models.AbstractModel):
    _name = "pms_api_supplier.supplier_router.helper"
    _inherit = "pms_api_contact.contact_router.helper"
    _description = "Pms api supplier Service Helper"

    def _get_domain_adapter(self):
        res = super()._get_domain_adapter()
        if res is None:
            res = []
        res = expression.AND([res, [("supplier_rank", ">", 0)]])
        return res
