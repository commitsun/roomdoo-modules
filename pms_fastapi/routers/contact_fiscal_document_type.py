from typing import Annotated

from fastapi import Depends

from odoo import models
from odoo.api import Environment

from odoo.addons.fastapi.dependencies import odoo_env
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.contact import ContactFiscalDocumentType


@pms_api_router.get(
    "/contact-fiscal-document",
    response_model=list[ContactFiscalDocumentType],
    tags=["db_info"],
)
async def get_fiscal_document_types(
    env: Annotated[Environment, Depends(odoo_env)],
) -> list[ContactFiscalDocumentType]:
    """
    Get country states configured in the instance.
    """
    fiscal_document_types = (
        env["pms_api_contact.contact_fiscal_document_type_router.helper"]
        .sudo()
        .get_fiscal_document_types()
    )
    return [
        ContactFiscalDocumentType(name=fiscal_document_type)
        for fiscal_document_type in fiscal_document_types
    ]


class PmsApiContactRouterHelper(models.AbstractModel):
    _name = "pms_api_contact.contact_fiscal_document_type_router.helper"

    def get_fiscal_document_types(self) -> list[str]:
        return ["vat"]
