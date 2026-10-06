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
    def _handle_error(cls, exception):
        """Let the caller read the errors of these APIs.

        A browser only hands a cross-origin response to the page when the
        response itself says that origin may read it. Successful answers get
        that permission on their way out, but error answers never pass through
        the place that grants it: the one of the legacy API is the werkzeug
        exception itself, which rebuilds its headers from scratch, and the one
        of the FastAPI app is built here by Odoo, on purpose, so that a failure
        rolls the transaction back instead of being turned into a response
        inside the application.

        The result was that every error of both APIs reached the browser
        stripped of that permission and was thrown away before the client could
        look at it: a wrong password could not be told apart from an
        unreachable server. It went unnoticed for a long time because the proxy
        used to stamp a wildcard permission on absolutely everything, error
        answers included; that wildcard had to go the day the client started
        sending cookies, since browsers refuse the two together.

        Where this is heading: an error is meant to be raised and left alone,
        so that the failure rolls the transaction back by itself instead of
        every endpoint remembering to wrap its own writes, with the FastAPI
        dispatcher extended to render it as problem+json, the way
        ``extendable_fastapi`` already extends it. That turns this into the way
        out for every error a client reads rather than for a handful of them,
        so it becomes more load-bearing, not less. Once the legacy API is gone
        it belongs in that same dispatcher, next to the rendering.
        """
        response = super()._handle_error(exception)
        cls._pms_api_rest_authorise_error(response)
        return response

    @classmethod
    def _pms_api_rest_authorise_error(cls, response):
        # Only the errors of the two APIs. Anything else, the backoffice
        # included, is served to its own origin and has no use for this. A
        # request that never matched a route keeps the default dispatcher, so
        # it is left out too: those are wrong URLs, not answers a client reads.
        if request.dispatcher.routing_type not in ("restapi", "fastapi"):
            return
        headers = cls._pms_api_rest_error_headers()
        if not headers:
            return
        if hasattr(response, "headers"):
            for key, value in headers:
                response.headers.set(key, value)
            return
        # A werkzeug exception is its own response and builds its headers when
        # it is asked for them, not before, so they have to be added there.
        build_headers = response.get_headers

        def get_headers(*args, **kwargs):
            return list(build_headers(*args, **kwargs)) + headers

        response.get_headers = get_headers

    @classmethod
    def _pms_api_rest_error_headers(cls):
        try:
            app_url = (
                request.env["ir.config_parameter"].sudo().get_param("roomdoo_app_url")
            )
        except Exception:
            # Whatever is being reported may be the very thing that left the
            # cursor unusable. Answering without the permission, as it happens
            # today, beats failing while trying to add it.
            return []
        if not app_url or app_url == "*":
            return []
        return [
            ("Access-Control-Allow-Origin", app_url),
            ("Access-Control-Allow-Credentials", "true"),
        ]

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
