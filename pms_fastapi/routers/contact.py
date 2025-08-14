from typing import Annotated

from fastapi import Depends

from odoo import models
from odoo.api import Environment

from odoo.addons.base.models.res_partner import Partner
from odoo.addons.fastapi.dependencies import (
    paging,
)
from odoo.addons.fastapi.schemas import PagedCollection, Paging
from odoo.addons.fastapi_auth_jwt.dependencies import AuthJwtOdooEnv
from odoo.addons.pms_fastapi.dependencies import create_order_dependency
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.contact import (
    CONTACT_ORDER_MAPPING,
    ContactOrderField,
    ContactSearch,
    ContactSummary,
)
from odoo.addons.pms_fastapi.utils import FilteredModelAdapter

ContactOrderDependency = create_order_dependency(
    ContactOrderField, CONTACT_ORDER_MAPPING, ["name"]
)


@pms_api_router.get(
    "/contacts", response_model=PagedCollection[ContactSummary], tags=["contact"]
)
async def list_contacts(
    env: Annotated[Environment, Depends(AuthJwtOdooEnv(validator_name="api_pms"))],
    filters: Annotated[ContactSearch, Depends()],
    paging: Annotated[Paging, Depends(paging)],
    orderBy: Annotated[str, Depends(ContactOrderDependency)],
) -> list[ContactSummary]:
    """Get the list of the contacts without differentiating type"""
    count, contacts = (
        env["pms_api_contact.contact_router.helper"]
        .new()
        ._search(paging, filters, orderBy)
    )

    return PagedCollection[ContactSummary](
        count=count,
        items=[ContactSummary.from_res_partner(contact) for contact in contacts],
    )


class PmsApiContactRouterHelper(models.AbstractModel):
    _name = "pms_api_contact.contact_router.helper"
    _description = "Pms api contact Service Helper"

    def _get_domain_adapter(self):
        return []

    @property
    def model_adapter(self) -> FilteredModelAdapter[Partner]:
        return FilteredModelAdapter[Partner](self.env, self._get_domain_adapter())

    def _get(self, record_id) -> Partner:
        return self.model_adapter.get(record_id)

    def _search(self, paging, params, order) -> tuple[int, Partner]:
        return self.model_adapter.search_with_count(
            params.to_odoo_domain(self.env),
            limit=paging.limit,
            offset=paging.offset,
            order=order,
        )
