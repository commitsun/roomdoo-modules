from odoo import models
from odoo.http import request


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    @classmethod
    def _pre_dispatch(cls, rule, args):
        res = super()._pre_dispatch(rule, args)
        cors = (
            request.env["ir.config_parameter"]
            .sudo()
            .get_param("roomdoo_app_url", default="*")
        )
        set_header = request.future_response.headers.set
        set_header("Access-Control-Allow-Credentials", "true")
        set_header("Access-Control-Expose-Headers", "set-cookie")
        set_header("Access-Control-Allow-Origin", cors)
        return res
