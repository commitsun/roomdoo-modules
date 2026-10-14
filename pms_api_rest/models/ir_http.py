import re

import werkzeug.exceptions
from werkzeug.datastructures import Headers

from odoo import api, models
from odoo.http import request

from ..controllers.pms_rest import BaseRestPrivateApiController

# Whatever reads best in a text field: commas, spaces or one origin per line.
ORIGIN_SEPARATORS = re.compile(r"[\s,]+")
# Values that name no origin: none of them is ever taken as one.
NOT_ORIGINS = ("", "*", "null")


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    @api.model
    def _pms_api_rest_allowed_origins(self):
        """Origins whose pages may call the PMS APIs from a browser.

        ``roomdoo_app_url``, the web app, comes first, followed by the origins
        in ``roomdoo_app_extra_origins``, for other clients served from their
        own host. Both APIs take their list from here so that they never
        disagree on who may call them. Origins are compared exactly, so the
        trailing slash a stored URL often carries, and an origin never does, is
        dropped. ``*`` and ``null`` are never taken as origins: the first would
        let any site in, credentials included, and the second is what sandboxed
        pages and local files send.

        An empty list means no restriction: without ``roomdoo_app_url``, or
        with ``*`` or ``null`` in it, both APIs keep answering any origin
        without credentials, and the extra origins are ignored.
        """
        get_param = self.env["ir.config_parameter"].sudo().get_param
        app_url = (get_param("roomdoo_app_url") or "").strip().rstrip("/")
        if app_url in NOT_ORIGINS:
            return []
        origins = [app_url]
        extra_origins = get_param("roomdoo_app_extra_origins") or ""
        for origin in ORIGIN_SEPARATORS.split(extra_origins):
            origin = origin.rstrip("/")
            if origin not in NOT_ORIGINS and origin not in origins:
                origins.append(origin)
        return origins

    @classmethod
    def _pms_api_rest_cors_headers(cls):
        """The CORS headers that let the calling page read the answer.

        ``Access-Control-Allow-Origin`` holds a single origin, so a request
        coming from one of the allowed origins is answered with its own. Any
        other keeps getting ``roomdoo_app_url``, which its browser refuses, as
        it always did.
        """
        origins = request.env["ir.http"]._pms_api_rest_allowed_origins()
        if not origins:
            return []
        origin = request.httprequest.headers.get("Origin")
        if origin not in origins:
            origin = origins[0]
        return [
            ("Access-Control-Allow-Origin", origin),
            ("Access-Control-Allow-Credentials", "true"),
        ]

    @classmethod
    def _pms_api_rest_write_cors_headers(cls, headers, cors_headers):
        for key, value in cors_headers:
            headers.set(key, value)
        # The answer now depends on the origin of the request: a cache must
        # keep one per origin, on top of whatever it already varies on.
        vary = [
            value.strip()
            for value in ",".join(headers.getlist("Vary")).split(",")
            if value.strip()
        ]
        if not any(value.lower() in ("origin", "*") for value in vary):
            headers.set("Vary", ", ".join([*vary, "Origin"]))

    @classmethod
    def _pms_api_rest_set_cors_headers(cls, rule):
        """Emit the CORS headers a cookie based login needs.

        Browsers reject a credentialed response whose allowed origin is ``*``,
        and they need ``Access-Control-Allow-Credentials``, which Odoo never
        emits. The origin cannot be stored in ``routing["cors"]`` because that
        dict belongs to the controller class and is shared by every database of
        the process, so it is written here, per request, overwriting the value
        ``Dispatcher.pre_dispatch`` already wrote. Per request is also what
        lets the answer name the origin the request comes from.
        """
        if not rule.rule.startswith(BaseRestPrivateApiController._root_path):
            return
        cors_headers = cls._pms_api_rest_cors_headers()
        if cors_headers:
            cls._pms_api_rest_write_cors_headers(
                request.future_response.headers, cors_headers
            )

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
            cls._pms_api_rest_write_cors_headers(response.headers, headers)
            return
        # A werkzeug exception is its own response and builds its headers when
        # it is asked for them, not before, so they have to be added there.
        build_headers = response.get_headers

        def get_headers(*args, **kwargs):
            built = Headers(build_headers(*args, **kwargs))
            cls._pms_api_rest_write_cors_headers(built, headers)
            return built.to_wsgi_list()

        response.get_headers = get_headers

    @classmethod
    def _pms_api_rest_error_headers(cls):
        try:
            return cls._pms_api_rest_cors_headers()
        except Exception:
            # Whatever is being reported may be the very thing that left the
            # cursor unusable. Answering without the permission, as it happens
            # today, beats failing while trying to add it.
            return []

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
