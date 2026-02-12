from typing import Annotated

from fastapi import Depends, HTTPException

from odoo import api, models
from odoo.exceptions import MissingError
from odoo.osv import expression

from odoo.addons.base.models.res_partner import Partner
from odoo.addons.extendable_fastapi.schemas import PagedCollection
from odoo.addons.fastapi.dependencies import (
    paging,
)
from odoo.addons.fastapi.schemas import Paging
from odoo.addons.pms_fastapi.dependencies import (
    AuthenticatedEnv,
    create_order_dependency,
)
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
    env: AuthenticatedEnv,
    filters: Annotated[ContactSearch, Depends()],
    paging: Annotated[Paging, Depends(paging)],
    orderBy: Annotated[str, Depends(ContactOrderDependency)],
) -> PagedCollection[ContactSummary]:
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
    env: AuthenticatedEnv,
) -> list[str]:
    return env["pms_api_contact.contact_router.helper"].extra_features()


@pms_api_router.get(
    "/contacts/{contact_id}",
    response_model=ContactDetail,
    tags=["contact"],
)
async def contactDetail(
    env: AuthenticatedEnv,
    contact_id: int,
) -> ContactDetail:
    """Get detail info of a contact"""
    helper = env["pms_api_contact.contact_router.helper"].new()
    try:
        partner = helper.get(contact_id)
    except MissingError as err:
        raise HTTPException(
            status_code=404,
            detail="contact not found",
        ) from err
    return ContactDetail.from_res_partner(partner)


@pms_api_router.post(
    "/contacts",
    response_model=ContactDetail,
    status_code=201,
    tags=["contact"],
)
async def create_contact(
    env: AuthenticatedEnv,
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
    env: AuthenticatedEnv,
    contact_id: int,
    contactData: ContactUpdate,
) -> ContactDetail:
    helper = env["pms_api_contact.contact_router.helper"].new()
    try:
        contact = helper.get(contact_id)
    except MissingError as err:
        raise HTTPException(
            status_code=404,
            detail="contact not found",
        ) from err
    helper.update_contact(contactData, contact_id)
    return ContactDetail.from_res_partner(contact)


class PmsApiContactRouterHelper(models.AbstractModel):
    _name = "pms_api_contact.contact_router.helper"
    _description = "Pms api contact Service Helper"

    def _get_domain_adapter(self):
        return [("type", "in", ["contact"])]

    def _get_multicompany_rule(self):
        allowed_company_ids = self.env.user.company_ids.ids
        company_domain = expression.OR(
            [
                [("company_id", "=", False)],
                [("company_id", "in", allowed_company_ids)],
            ]
        )
        return company_domain

    @property
    def model_adapter(self) -> FilteredModelAdapter[Partner]:
        base_domain = self._get_domain_adapter()
        multicompany_domain = self._get_multicompany_rule()
        model_domain = expression.AND([base_domain, multicompany_domain])
        return FilteredModelAdapter[Partner](self.env, model_domain)

    def get(self, record_id) -> Partner:
        return self.model_adapter.get(record_id)

    def _search(self, paging, params, order) -> tuple[int, Partner]:
        return self.model_adapter.search_with_count(
            params.to_odoo_domain(self.env),
            limit=paging.limit,
            offset=paging.offset,
            order=order,
            context=params.to_odoo_context(self.env),
        )

    def count(self, params=None) -> int:
        if params:
            domain = params.to_odoo_domain(self.env)
        else:
            domain = []
        return self.model_adapter.count(domain)

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
        res = self.env["res.partner"].sudo().create(vals)
        if data.fiscalIdNumberType or data.fiscalIdNumber:
            res.set_fiscal_document_data(data.fiscalIdNumber, data.fiscalIdNumberType)
        return res

    def update_contact(self, data: ContactUpdate, contact_id: int):
        vals = self._prepare_write_res_partner_vals(data)
        partner = self.env["res.partner"].sudo().browse(contact_id)
        res = partner.write(vals)
        if data.fiscalIdNumberType or data.fiscalIdNumber:
            partner.set_fiscal_document_data(
                data.fiscalIdNumber, data.fiscalIdNumberType
            )
        return res
