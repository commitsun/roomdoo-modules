from odoo.addons.pms_fastapi.dependencies import AuthenticatedEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.payment_method import PaymentMethodSummary


@pms_api_router.get(
    "/payment-methods",
    response_model=list[PaymentMethodSummary],
    tags=["account"],
)
async def list_payment_methods(
    env: AuthenticatedEnv,
) -> list[PaymentMethodSummary]:
    """List all payment methods."""
    methods = env["account.payment.method"].sudo().search([])
    return [
        PaymentMethodSummary.from_account_payment_method(method) for method in methods
    ]
