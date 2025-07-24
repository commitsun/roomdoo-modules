from typing import Annotated
from pydantic import AnyHttpUrl
from fastapi import Depends, HTTPException, Response, status

from odoo.api import Environment
from odoo.addons.fastapi_auth_jwt.dependencies import AuthJwtOdooEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router

@pms_api_router.get(
    "/properties/{id}/menus/{menu_id}",
    status_code=200,
    responses={
        404: {
            "description": "Resource not found",
            "content": {"application/json": {"example": {"detail": "property not found"}}},
        },
        200: {"model": None},
    },
    response_model=AnyHttpUrl,
    tags=["property"],
)
async def get_property_menu_url(
    id: int,
    menu_id: int,
    env: Annotated[Environment, Depends(AuthJwtOdooEnv(validator_name="api_pms"))]
) -> AnyHttpUrl:
    property_obj = env['pms.property'].search([('id', '=', id)])
    if not property_obj:
        raise HTTPException(
            status_code=404,
            detail="property not found",
        )

    menu = env['roomdoo.app.menu'].search([('id', '=', menu_id)])
    if not menu:
        raise HTTPException(
            status_code=404,
            detail="menu not found",
        )
    final_url = menu.generate_url(property_obj)
    return final_url



