from typing import Annotated

from fastapi import Depends, HTTPException, Response, status

from odoo import models
from odoo.api import Environment

from odoo.addons.fastapi_auth_jwt.dependencies import AuthJwtOdooEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.contact import ContactId
from odoo.addons.pms_fastapi.schemas.contact_id_number import (
    ContactIdNumberCategorySummary,
    ContactIdNumberInsert,
    ContactIdNumberSummary,
    ContactIdNumberUpdate,
)


@pms_api_router.get(
    "/contacts/duplicate/id-numbers",
    response_model=ContactId,
    responses={
        204: {"description": "No duplicate identification number found"},
    },
    tags=["utilities"],
)
async def get_duplicate_id_numbers(
    env: Annotated[Environment, Depends(AuthJwtOdooEnv(validator_name="api_pms"))],
    category: int,
    number: str,
    country: int,
) -> ContactId | Response:
    """
    Get duplicate contact by identification number. Should be called before
    creating or updating a contact id number.
    """
    duplicate_partner = (
        env["res.partner.id_number"]
        .sudo()
        .get_duplicate(
            number,
            env["res.partner.id_category"].sudo().browse(category),
            env["res.country"].sudo().browse(country),
        )
    )
    if duplicate_partner:
        return ContactId.from_res_partner(duplicate_partner)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@pms_api_router.get(
    "/contacts/duplicate/fiscal-number",
    response_model=ContactId,
    responses={
        204: {"description": "No duplicate fiscal number found"},
    },
    tags=["utilities"],
)
async def get_duplicate_fiscal_number(
    env: Annotated[Environment, Depends(AuthJwtOdooEnv(validator_name="api_pms"))],
    type: str,
    number: str,
    country: int | None = None,
) -> ContactId | Response:
    """
    Get duplicate contact by fiscal number. Should be called before
    creating or updating a contact fiscal number.
    """
    helper = env["pms_api_contact.contact_id_number_router.helper"].new()
    duplicate_partner = helper.get_duplicate_fiscal_number(number, type, country)
    if duplicate_partner:
        return ContactId.from_res_partner(duplicate_partner)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@pms_api_router.get(
    "/id-number-categories",
    response_model=list[ContactIdNumberCategorySummary],
    tags=["contact_id_number"],
)
async def list_id_number_categories(
    env: Annotated[Environment, Depends(AuthJwtOdooEnv(validator_name="api_pms"))],
    country: int | None = None,
) -> list[ContactIdNumberCategorySummary]:
    category_search_domain = []
    if country:
        category_search_domain = [
            "|",
            ("country_ids", "=", False),
            ("country_ids", "in", [country]),
        ]
    categories = env["res.partner.id_category"].sudo().search(category_search_domain)
    return [
        ContactIdNumberCategorySummary.from_res_partner_id_number_category(category)
        for category in categories
    ]


@pms_api_router.get(
    "/contacts/{contact_id}/id-numbers",
    response_model=list[ContactIdNumberSummary],
    tags=["contact_id_number"],
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
            detail="contact not found",
        )
    id_numbers = []
    for id_number in partner.id_numbers:
        id_numbers.append(ContactIdNumberSummary.from_res_partner_id_number(id_number))
    return id_numbers


@pms_api_router.post(
    "/contacts/{contact_id}/id-numbers",
    response_model=ContactIdNumberSummary,
    status_code=201,
    tags=["contact_id_number"],
)
async def create_contact_id_number(
    env: Annotated[Environment, Depends(AuthJwtOdooEnv(validator_name="api_pms"))],
    contact_id: int,
    idNumberData: ContactIdNumberInsert,
) -> ContactIdNumberSummary:
    helper = env["pms_api_contact.contact_id_number_router.helper"].new()
    new_id_number = helper.create_id_number(contact_id, idNumberData)
    return ContactIdNumberSummary.from_res_partner_id_number(new_id_number)


@pms_api_router.patch(
    "/contacts/{contact_id}/id-numbers/{idNumber_id}",
    response_model=ContactIdNumberSummary,
    tags=["contact_id_number"],
)
async def update_contact_id_number(
    env: Annotated[Environment, Depends(AuthJwtOdooEnv(validator_name="api_pms"))],
    contact_id: int,
    idNumber_id: int,
    idNumberData: ContactIdNumberUpdate,
) -> ContactIdNumberSummary:
    id_number = env["res.partner.id_number"].sudo().browse(idNumber_id)
    if id_number.partner_id.id != contact_id:
        raise HTTPException(
            status_code=400,
            detail=(
                f"The id number {idNumber_id} does not belong "
                f"to the contact {contact_id}"
            ),
        )
    helper = env["pms_api_contact.contact_id_number_router.helper"].new()
    helper.write_id_number(id_number, idNumberData)
    return ContactIdNumberSummary.from_res_partner_id_number(id_number)


@pms_api_router.put(
    "/contacts/{contact_id}/id-numbers/{idNumber_id}/set-fiscal-number",
    response_model=ContactIdNumberSummary,
    tags=["contact_id_number"],
)
async def set_fiscal_number_contact_id_number(
    env: Annotated[Environment, Depends(AuthJwtOdooEnv(validator_name="api_pms"))],
    contact_id: int,
    idNumber_id: int,
) -> ContactIdNumberSummary:
    id_number = env["res.partner.id_number"].sudo().browse(idNumber_id)
    if id_number.partner_id.id != contact_id:
        raise HTTPException(
            status_code=400,
            detail=(
                f"The id number {idNumber_id} does not belong "
                f"to the contact {contact_id}"
            ),
        )
    id_number.sudo().set_partner_id_field()
    return ContactIdNumberSummary.from_res_partner_id_number(id_number)


@pms_api_router.delete(
    "/contacts/{contact_id}/id-numbers/{idNumber_id}",
    status_code=204,
    tags=["contact_id_number"],
)
async def delete_contact_id_number(
    env: Annotated[Environment, Depends(AuthJwtOdooEnv(validator_name="api_pms"))],
    contact_id: int,
    idNumber_id: int,
):
    id_number = env["res.partner.id_number"].sudo().browse(idNumber_id)
    if id_number.partner_id.id != contact_id:
        raise HTTPException(
            status_code=400,
            detail=(
                f"The id number {idNumber_id} does not belong "
                f"to the contact {contact_id}"
            ),
        )
    id_number.sudo().unlink()


class PmsApiContactIdNumberRouterHelper(models.AbstractModel):
    _name = "pms_api_contact.contact_id_number_router.helper"
    _description = "Pms api contact  id number Service Helper"

    def get_duplicate_fiscal_number(
        self, fiscal_number: str, document_type: str, country_id: int | None = None
    ):
        if document_type == "vat":
            country = self.env["res.country"].browse(country_id) if country_id else None
            return (
                self.env["res.partner"].sudo().get_duplicate_vat(fiscal_number, country)
            )

    def _prepare_create_id_number_vals(
        self,
        contact_id: int,
        data: ContactIdNumberInsert,
    ):
        return data.to_res_partner_id_number(contact_id)

    def _prepare_update_id_number_vals(
        self,
        data: ContactIdNumberUpdate,
    ):
        return data.to_res_partner_id_number()

    def create_id_number(self, contact_id: int, data: ContactIdNumberInsert):
        vals = self._prepare_create_id_number_vals(contact_id, data)
        return self.env["res.partner.id_number"].sudo().create(vals)

    def write_id_number(self, idNumber, data: ContactIdNumberUpdate):
        vals = self._prepare_update_id_number_vals(data)
        idNumber.write(vals)
