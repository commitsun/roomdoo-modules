from odoo.addons.pms_fastapi.dependencies import AuthenticatedEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.pms_service import ServiceProduct


@pms_api_router.get(
    "/services",
    response_model=list[ServiceProduct],
    tags=["service"],
)
async def get_services(
    env: AuthenticatedEnv,
) -> list[ServiceProduct]:
    """
    List PMS service products available for folio filtering.
    """
    products = (
        env["product.product"]
        .sudo()
        .search([("is_pms_available", "=", True)], order="name")
    )
    return [ServiceProduct.from_product_product(product) for product in products]
