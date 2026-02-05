from odoo import api, fields, models
from odoo.exceptions import MissingError


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    roomdoo_fastapi_instance_name = fields.Char(
        "Roomdoo FastAPI Instance Name", help="Name of the Roomdoo FastAPI instance"
    )
    roomdoo_fastapi_image = fields.Binary(
        "Roomdoo FastAPI Image", help="Image of the Roomdoo FastAPI instance"
    )

    @api.model
    def get_values(self):
        config_parameter_obj_sudo = self.env["ir.config_parameter"].sudo()
        res = super().get_values()
        res["roomdoo_fastapi_instance_name"] = config_parameter_obj_sudo.get_param(
            "roomdoo_fastapi.instance_name", default="Roomdoo"
        )
        image_parameter = config_parameter_obj_sudo.get_param(
            "roomdoo_fastapi.instance_image", default=False
        )
        if image_parameter:
            image_attachment = (
                self.env["ir.attachment"].sudo().browse(int(image_parameter))
            )
            try:
                image_attachment.datas  # noqa: B018  # Access to check if attachment exists
            except MissingError:
                image_attachment = False
            res["roomdoo_fastapi_image"] = (
                image_attachment.datas if image_attachment else False
            )
        return res

    @api.model
    def set_values(self):
        config_parameter_obj_sudo = self.env["ir.config_parameter"].sudo()
        res = super().set_values()
        config_parameter_obj_sudo.set_param(
            "roomdoo_fastapi.instance_name", self.roomdoo_fastapi_instance_name
        )
        image_parameter = config_parameter_obj_sudo.get_param(
            "roomdoo_fastapi.instance_image", default=False
        )
        image_attachment = self.env["ir.attachment"].sudo().browse(int(image_parameter))
        if not self.roomdoo_fastapi_image:
            if image_attachment:
                image_attachment.unlink()
            if image_parameter:
                config_parameter_obj_sudo.set_param(
                    "roomdoo_fastapi.instance_image", False
                )
            return res
        if self.roomdoo_fastapi_image:
            if not image_attachment:
                image_attachment = (
                    self.env["ir.attachment"]
                    .sudo()
                    .create(
                        {
                            "name": "roomdoo_fastapi_image",
                            "datas": self.roomdoo_fastapi_image,
                        }
                    )
                )
            else:
                image_attachment.datas = self.roomdoo_fastapi_image
            config_parameter_obj_sudo.set_param(
                "roomdoo_fastapi.instance_image", image_attachment.id
            )
        return res
