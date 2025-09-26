from typing import Annotated

from fastapi import Depends

from odoo.api import Environment

from odoo.addons.fastapi.dependencies import odoo_env
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.payment_term import PaymentTermId


@pms_api_router.get(
    "/payment-terms", response_model=list[PaymentTermId], tags=["account"]
)
async def get_payment_terms(
    env: Annotated[Environment, Depends(odoo_env)]
) -> list[PaymentTermId]:
    """
    Get payment terms configured in the instance.
    """
    payment_terms = env["account.payment.term"].sudo().search([])
    return [
        PaymentTermId.from_account_payment_term(payment_term)
        for payment_term in payment_terms
    ]
