from typing import Annotated

from fastapi import Depends

from odoo.api import Environment

from odoo.addons.fastapi.dependencies import odoo_env
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.base import PmsBaseModel
from odoo.addons.roomdoo_fastapi.schemas.instance import Instance


@pms_api_router.get("/instance", response_model=Instance, tags=["db_info"])
async def get_instance_info(env: Annotated[Environment, Depends(odoo_env)]) -> Instance:
    """
    Get instance name and image URL.
    """
    instance_name = (
        env["ir.config_parameter"]
        .sudo()
        .get_param("roomdoo_fastapi.instance_name", default="Roomdoo")
    )
    instance_image = (
        env["ir.config_parameter"].sudo().get_param("roomdoo_fastapi.instance_image")
    )
    if instance_image:
        image_attachment = env["ir.attachment"].sudo().browse(int(instance_image))
        instance_image = PmsBaseModel.get_attachment_url(env, image_attachment)
    if not instance_image:
        web_base_url = env["ir.config_parameter"].sudo().get_param("web.base.url")
        instance_image = f"{web_base_url}/web/binary/company_logo"
    return Instance(name=instance_name, image=instance_image)
