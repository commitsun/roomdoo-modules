from typing import Annotated

from fastapi import Depends, HTTPException

from odoo import api, models
from odoo.api import Environment

from odoo.addons.base.models.res_partner import Partner
from odoo.addons.extendable_fastapi.schemas import PagedCollection
from odoo.addons.fastapi.dependencies import (
    paging,
)
from odoo.addons.fastapi.schemas import Paging
from odoo.addons.fastapi_auth_jwt.dependencies import AuthJwtOdooEnv
from odoo.addons.pms_fastapi.dependencies import create_order_dependency
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.contact import (
    CONTACT_ORDER_MAPPING,
    ContactDetail,
    ContactOrderField,
    ContactSearch,
    ContactSummary,
)
from odoo.addons.pms_fastapi.schemas.contact_id_number import ContactIdNumberSummary
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


@pms_api_router.get(
    "/contacts/extra-features", response_model=list[str], tags=["contact"]
)
async def contact_extra_features(
    env: Annotated[Environment, Depends(AuthJwtOdooEnv(validator_name="api_pms"))],
) -> list[str]:
    return env["pms_api_contact.contact_router.helper"].extra_features()


@pms_api_router.get(
    "/contacts/{contact_id}", response_model=ContactDetail, tags=["contact"]
)
async def contactDetail(
    env: Annotated[Environment, Depends(AuthJwtOdooEnv(validator_name="api_pms"))],
    contact_id: int,
) -> ContactDetail:
    """Get detail info of a contact"""
    partner = env["res.partner"].sudo().search([("id", "=", contact_id)])
    if not partner:
        raise HTTPException(
            status_code=404,
            detail="property not found",
        )
    ContactDetail.pms_api_check_access(env.user, partner)
    return ContactDetail.from_res_partner(partner)


@pms_api_router.get(
    "/contacts/{contact_id}/id-numbers",
    response_model=list[ContactIdNumberSummary],
    tags=["contact"],
)
async def contact_id_numbers(
    env: Annotated[Environment, Depends(AuthJwtOdooEnv(validator_name="api_pms"))],
    contact_id: int,
) -> list[ContactIdNumberSummary]:
    """Get identification numbers of a contact"""
    partner = env["res.partner"].sudo().search([("id", "=", contact_id)])
    if not partner:
        raise HTTPException(
            status_code=404,
            detail="property not found",
        )
    id_numbers = []
    for id_number in partner.id_numbers:
        id_numbers.append(ContactIdNumberSummary.from_res_partner_id_number(id_number))
    return id_numbers


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

    @api.model
    def extra_features(self):
        return []
