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
    board_product_ids = set()
    for model in ("pms.board.service.line", "pms.board.service.room.type.line"):
        groups = env[model].sudo().read_group([], ["product_id"], ["product_id"])
        board_product_ids |= {g["product_id"][0] for g in groups if g["product_id"]}
    return [
        ServiceProduct.from_product_product(
            product, is_board_service=product.id in board_product_ids
        )
        for product in products
    ]
