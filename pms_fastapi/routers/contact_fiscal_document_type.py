from odoo import models

from odoo.addons.pms_fastapi.dependencies import AuthenticatedEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.contact import ContactFiscalDocumentType


@pms_api_router.get(
    "/contact-fiscal-document",
    response_model=list[ContactFiscalDocumentType],
    tags=["db_info"],
)
async def get_fiscal_document_types(
    env: AuthenticatedEnv,
) -> list[ContactFiscalDocumentType]:
    """
    Get fiscal document types configured in the instance.
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


class PmsApiFiscalDocumentTypeRouterHelper(models.AbstractModel):
    _name = "pms_api_contact.contact_fiscal_document_type_router.helper"
    _description = "PMS API Fiscal Document Type Router Helper"

    def get_fiscal_document_types(self) -> list[str]:
        return ["vat"]
