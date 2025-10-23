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
    ContactInsert,
    ContactOrderField,
    ContactSearch,
    ContactSummary,
    ContactUpdate,
)
from odoo.addons.pms_fastapi.utils import FilteredModelAdapter

ContactOrderDependency = create_order_dependency(
    ContactOrderField, CONTACT_ORDER_MAPPING, ["name"]
)


@pms_api_router.get(
    "/contacts",
    response_model=PagedCollection[ContactSummary],
    tags=["contact"],
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
    "/contacts/{contact_id}",
    response_model=ContactDetail,
    tags=["contact"],
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
            detail="contact not found",
        )
    ContactDetail.pms_api_check_access(env.user, partner)
    return ContactDetail.from_res_partner(partner)


@pms_api_router.post(
    "/contacts",
    response_model=ContactDetail,
    tags=["contact"],
)
async def create_contact(
    env: Annotated[Environment, Depends(AuthJwtOdooEnv(validator_name="api_pms"))],
    contactData: ContactInsert,
) -> ContactDetail:
    helper = env["pms_api_contact.contact_router.helper"].new()
    new_contact = helper.create_contact(contactData)
    return ContactDetail.from_res_partner(new_contact)


@pms_api_router.patch(
    "/contacts/{contact_id}",
    response_model=ContactDetail,
    tags=["contact"],
)
async def update_contact(
    env: Annotated[Environment, Depends(AuthJwtOdooEnv(validator_name="api_pms"))],
    contact_id: int,
    contactData: ContactUpdate,
) -> ContactDetail:
    helper = env["pms_api_contact.contact_router.helper"].new()
    helper.update_contact(contactData, contact_id)
    contact = env["res.partner"].sudo().search([("id", "=", contact_id)])
    if not contact:
        raise HTTPException(
            status_code=404,
            detail="contact not found",
        )
    ContactDetail.pms_api_check_access(env.user, contact)
    return ContactDetail.from_res_partner(contact)


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
            context=params.to_odoo_context(self.env),
        )

    @api.model
    def extra_features(self):
        return []

    def _prepare_create_res_partner_vals(
        self,
        data: ContactInsert,
    ):
        return data.to_res_partner()

    def _prepare_write_res_partner_vals(
        self,
        data: ContactUpdate,
    ):
        return data.to_res_partner()

    def create_contact(self, data: ContactInsert):
        vals = self._prepare_create_res_partner_vals(data)
        return self.env["res.partner"].sudo().create(vals)

    def update_contact(self, data: ContactUpdate, contact_id: int):
        vals = self._prepare_write_res_partner_vals(data)
        return self.env["res.partner"].sudo().browse(contact_id).write(vals)
