import werkzeug.exceptions

from odoo import models
from odoo.http import request

from ..controllers.pms_rest import BaseRestPrivateApiController


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    @classmethod
    def _pms_api_rest_set_cors_headers(cls, rule):
        """Emit the CORS headers a cookie based login needs.

        Browsers reject a credentialed response whose allowed origin is ``*``,
        and they need ``Access-Control-Allow-Credentials``, which Odoo never
        emits. The origin cannot be stored in ``routing["cors"]`` because that
        dict belongs to the controller class and is shared by every database of
        the process, so it is written here, per request, overwriting the value
        ``Dispatcher.pre_dispatch`` already wrote.
        """
        if not rule.rule.startswith(BaseRestPrivateApiController._root_path):
            return
        app_url = request.env["ir.config_parameter"].sudo().get_param("roomdoo_app_url")
        if not app_url or app_url == "*":
            return
        set_header = request.future_response.headers.set
        set_header("Access-Control-Allow-Origin", app_url)
        set_header("Access-Control-Allow-Credentials", "true")

    @classmethod
    def _pre_dispatch(cls, rule, args):
        try:
            res = super()._pre_dispatch(rule, args)
        except werkzeug.exceptions.HTTPException:
            # A CORS preflight aborts inside ``Dispatcher.pre_dispatch``. Its 204
            # response still carries whatever is left in ``future_response``, so
            # the headers have to be written before letting the abort through.
            cls._pms_api_rest_set_cors_headers(rule)
            raise
        cls._pms_api_rest_set_cors_headers(rule)
        return res
