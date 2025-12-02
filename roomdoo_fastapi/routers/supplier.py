from typing import Annotated

from fastapi import Depends

from odoo.api import Environment

from odoo.addons.fastapi_auth_jwt.dependencies import AuthJwtOdooEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router


@pms_api_router.get(
    "/suppliers-count",
    response_model=int,
    tags=["contact"],
)
async def count_suppliers(
    env: Annotated[Environment, Depends(AuthJwtOdooEnv(validator_name="api_pms"))],
) -> int:
    return env["pms_api_supplier.supplier_router.helper"].new().count()
