from typing import Annotated

from fastapi import Depends, HTTPException
from pydantic import AnyHttpUrl

from odoo.api import Environment

from odoo.addons.fastapi_auth_jwt.dependencies import AuthJwtOdooEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.roomdoo_fastapi.schemas.property_link import PropertyLink


@pms_api_router.get(
    "/pms-properties/{property_id}/links",
    status_code=200,
    responses={
        200: {"model": None},
    },
    response_model=list[PropertyLink],
    tags=["property"],
)
async def get_property_links(
    property_id: int,
    env: Annotated[Environment, Depends(AuthJwtOdooEnv(validator_name="api_pms"))],
) -> list[PropertyLink]:
    """
    Returns a list of links of the property
    """
    property_obj = env["pms.property"].sudo().search([("id", "=", property_id)])
    if not property_obj:
        raise HTTPException(
            status_code=404,
            detail="property not found",
        )
    menus = property_obj.get_roomdoo_app_menu() + property_obj.get_roomdoo_support_url()
    return [PropertyLink.from_pms_property_menu(menu) for menu in menus]


@pms_api_router.get(
    "/pms-properties/{property_id}/links/{link_id}",
    status_code=200,
    responses={
        404: {
            "description": "Resource not found",
            "content": {
                "application/json": {"example": {"detail": "property not found"}}
            },
        },
        200: {"model": None},
    },
    response_model=AnyHttpUrl,
    tags=["property"],
)
async def get_property_link_url(
    property_id: int,
    link_id: int,
    env: Annotated[Environment, Depends(AuthJwtOdooEnv(validator_name="api_pms"))],
) -> AnyHttpUrl:
    """
    returns the final url for the given link id
    """
    property_obj = env["pms.property"].sudo().search([("id", "=", property_id)])
    if not property_obj:
        raise HTTPException(
            status_code=404,
            detail="property not found",
        )

    menu = env["roomdoo.app.menu"].sudo().search([("id", "=", link_id)])
    if not menu:
        raise HTTPException(
            status_code=404,
            detail="menu not found",
        )
    final_url = menu.sudo().generate_url(property_obj)
    return final_url
