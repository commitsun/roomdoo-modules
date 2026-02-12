from odoo.addons.pms_fastapi.dependencies import PublicEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.base import PmsBaseModel
from odoo.addons.roomdoo_fastapi.schemas.instance import Instance


@pms_api_router.get("/instance", response_model=Instance, tags=["db_info"])
async def get_instance_info(env: PublicEnv) -> Instance:
    """
    Get instance name and image URL.
    Public endpoint (no auth required) - used on the login screen.
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
        instance_image = None
    return Instance(name=instance_name, image=instance_image)
