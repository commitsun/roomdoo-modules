from odoo.addons.pms_fastapi.dependencies import AuthenticatedEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router


@pms_api_router.get(
    "/customers-count",
    response_model=int,
    tags=["contact"],
)
async def count_customers(
    env: AuthenticatedEnv,
) -> int:
    return env["pms_api_customer.customer_router.helper"].new().count()
