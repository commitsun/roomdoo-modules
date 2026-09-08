from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.responses import JSONResponse

from odoo import _, api, models
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
    ContactTypeDetail,
    ContactUpdate,
)
from odoo.addons.pms_fastapi.utils import (
    ApiProblem,
    FilteredModelAdapter,
    build_problem,
)

ContactOrderDependency = create_order_dependency(
    ContactOrderField, CONTACT_ORDER_MAPPING, ["name"]
)


class _ContactProblem(ApiProblem):
    """Contact-router problem, caught by this router's local handlers."""


# Both bodies a rejected contact payload can come back with: the endpoint's own
# name errors, and the request validation error of any malformed payload.
NAME_ERROR_RESPONSES = {
    422: {
        "description": "The payload was rejected. As application/problem+json "
        "when the name does not fit the contact type: type "
        "/errors/contact-lastname-not-applicable (last names sent for a "
        "company, with the offending field in `field`) or "
        "/errors/contact-name-required (the contact would be left with no "
        "name). As application/json with the request validation error when the "
        "payload itself is malformed, such as an unknown field.",
        "content": {
            "application/problem+json": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string"},
                        "title": {"type": "string"},
                        "status": {"type": "integer"},
                        "detail": {"type": "string"},
                        "field": {"type": "string"},
                    },
                },
            },
            "application/json": {
                "schema": {"$ref": "#/components/schemas/HTTPValidationError"},
            },
        },
    }
}


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
    """Get detail info of a contact.

    The name is split the way the contact type allows: for a person, `name` is
    their given name and `lastname` their last names; a company has a single
    `name` and no last names. The contact listings return the full name
    instead."""
    helper = env["pms_api_contact.contact_router.helper"].new()
    partner = helper.get_or_404(contact_id)
    return ContactDetail.from_res_partner(partner)


@pms_api_router.post(
    "/contacts",
    response_model=ContactDetail,
    status_code=201,
    responses=NAME_ERROR_RESPONSES,
    tags=["contact"],
)
async def create_contact(
    env: AuthenticatedEnv,
    contactData: ContactInsert,
) -> ContactDetail | JSONResponse:
    """Create a contact.

    `contactType` decides how the name is kept: a person has a given name in
    `name` plus their last names, while a company has a single `name` and no
    last names."""
    helper = env["pms_api_contact.contact_router.helper"].new()
    try:
        new_contact = helper.create_contact(contactData)
    except _ContactProblem as problem:
        return problem.response
    return ContactDetail.from_res_partner(new_contact)


@pms_api_router.patch(
    "/contacts/{contact_id}",
    response_model=ContactDetail,
    responses=NAME_ERROR_RESPONSES,
    tags=["contact"],
)
async def update_contact(
    env: AuthenticatedEnv,
    contact_id: int,
    contactData: ContactUpdate,
) -> ContactDetail | JSONResponse:
    """Update a contact, changing only what the payload carries.

    `contactType` decides how the name is kept: a person has a given name in
    `name` plus their last names, while a company has a single `name` and no
    last names. Changing the type without sending a name keeps the name the
    contact already shows."""
    helper = env["pms_api_contact.contact_router.helper"].new()
    contact = helper.get_or_404(contact_id)
    try:
        helper.update_contact(contactData, contact_id)
    except _ContactProblem as problem:
        return problem.response
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

    def get_or_404(self, contact_id) -> Partner:
        try:
            return self.get(contact_id)
        except MissingError as err:
            raise HTTPException(
                status_code=404,
                detail=_("contact not found"),
            ) from err

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

    @staticmethod
    def _problem(status_code, type_, title, detail, **extra):
        raise _ContactProblem(build_problem(status_code, type_, title, detail, **extra))

    def _is_company(self, data, partner=None):
        """Type the contact ends up with, which the payload may not carry."""
        if data.contactType:
            return data.contactType == ContactTypeDetail.company
        return bool(partner) and partner.is_company

    def _check_surnames_applicable(self, data, is_company):
        if not is_company:
            return
        for field in data.surname_fields():
            # A non-empty value can only come from the payload: they default
            # to empty
            if getattr(data, field):
                self._problem(
                    422,
                    "/errors/contact-lastname-not-applicable",
                    _("Last name not applicable"),
                    _("A company contact has a single name and no last names."),
                    field=field,
                )

    def _check_name_present(self, data, name_vals, is_company, partner=None):
        if is_company:
            name = name_vals.get("name", partner.name if partner else "")
            if not name:
                self._problem(
                    422,
                    "/errors/contact-name-required",
                    _("Name required"),
                    _("A company contact needs a name."),
                    field="name",
                )
            return
        for field in ("firstname",) + tuple(data.surname_fields()):
            if field in name_vals:
                value = name_vals[field]
            elif partner and field in partner._fields:
                value = partner[field]
            else:
                value = False
            if value:
                return
        self._problem(
            422,
            "/errors/contact-name-required",
            _("Name required"),
            _("A contact needs a name or a last name."),
            field="name",
        )

    def _prepare_name_vals(self, data, partner=None):
        """Validated values to store the name of the contact."""
        is_company = self._is_company(data, partner)
        self._check_surnames_applicable(data, is_company)
        vals = data.name_vals(is_company, partner)
        self._check_name_present(data, vals, is_company, partner)
        # The extendable schema registry is shared between databases, so a last
        # name field may come from a module that is not installed in this one.
        partner_fields = self.env["res.partner"]._fields
        return {name: value for name, value in vals.items() if name in partner_fields}

    def _prepare_create_res_partner_vals(
        self,
        data: ContactInsert,
    ):
        vals = data.to_res_partner()
        vals.update(self._prepare_name_vals(data))
        return vals

    def _prepare_write_res_partner_vals(
        self,
        data: ContactUpdate,
        partner=None,
    ):
        vals = data.to_res_partner()
        vals.update(self._prepare_name_vals(data, partner))
        return vals

    def create_contact(self, data: ContactInsert):
        vals = self._prepare_create_res_partner_vals(data)
        res = self.env["res.partner"].sudo().create(vals)
        if data.fiscalIdNumberType or data.fiscalIdNumber:
            res.set_fiscal_document_data(data.fiscalIdNumber, data.fiscalIdNumberType)
        return res

    def update_contact(self, data: ContactUpdate, contact_id: int):
        partner = self.env["res.partner"].sudo().browse(contact_id)
        vals = self._prepare_write_res_partner_vals(data, partner)
        res = partner.write(vals)
        if data.fiscalIdNumberType or data.fiscalIdNumber:
            partner.set_fiscal_document_data(
                data.fiscalIdNumber, data.fiscalIdNumberType
            )
        return res
