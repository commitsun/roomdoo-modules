# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import werkzeug.exceptions

from odoo import models
from odoo.http import request

from odoo.addons.pms_api_rest.controllers.pms_rest import (
    BaseRestPrivateApiController,
)

CARD_PASSWORD_HEADER = "X-Card-Password"


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    @classmethod
    def _pre_dispatch(cls, rule, args):
        try:
            return super()._pre_dispatch(rule, args)
        except werkzeug.exceptions.HTTPException:
            cls._pms_payment_card_complete_preflight(rule)
            raise

    @classmethod
    def _pms_payment_card_complete_preflight(cls, rule):
        """Let the browser send the card password and use every method.

        The front runs on another origin, so the browser asks first, with an
        OPTIONS request, whether it may send ``X-Card-Password``. Odoo answers
        that preflight with a fixed list of headers, and a header that is not
        in it never reaches the server.

        base_rest also makes a route per HTTP method, all of them sharing the
        same path, and Odoo answers the preflight with the methods of the
        first one it matches. The answer has to list all the methods of the
        path, or a PATCH or a DELETE is blocked whenever the GET comes first.
        """
        if request.httprequest.method != "OPTIONS" or not rule.rule.startswith(
            BaseRestPrivateApiController._root_path
        ):
            return
        headers = request.future_response.headers
        allowed_headers = headers.get("Access-Control-Allow-Headers")
        if allowed_headers is None:
            return
        if CARD_PASSWORD_HEADER.lower() not in allowed_headers.lower():
            headers.set(
                "Access-Control-Allow-Headers",
                f"{allowed_headers}, {CARD_PASSWORD_HEADER}",
            )
        adapter = (
            request.env["ir.http"]
            .routing_map()
            .bind_to_environ(request.httprequest.environ)
        )
        methods = sorted(set(adapter.allowed_methods()) - {"HEAD"})
        if methods:
            headers.set("Access-Control-Allow-Methods", ", ".join(methods))
