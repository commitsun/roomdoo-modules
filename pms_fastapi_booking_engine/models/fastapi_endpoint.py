# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from fastapi import APIRouter, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException

from odoo import fields, models

from odoo.addons.fastapi.context import odoo_env_ctx

APP_NAME = "pms_booking_engine"


class ProblemDetailsMiddleware:
    """Answer the request errors as problem+json, inside the app.

    The fastapi addon lets every error escape the app, and Odoo answers it
    without the CORS headers of the app, so the browser on the hotel website
    could not read it. Invalid parameters and unknown routes are raised before
    any database work, so they are safe to answer here; any other error still
    goes to Odoo, which rolls the transaction back.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started = False

        async def send_wrapper(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except (RequestValidationError, HTTPException) as error:
            if started:
                raise
            await self._get_problem(error)(scope, receive, send)

    def _get_problem(self, error):
        helper = odoo_env_ctx.get()["pms_booking_engine.availability_router.helper"]
        if isinstance(error, RequestValidationError):
            errors = error.errors()
            field_name = str(errors[0]["loc"][-1]) if errors else ""
            return helper._invalid_parameter_problem(field_name)
        return helper._http_error_problem(
            error.status_code, error.detail, headers=error.headers
        )


class FastapiEndpoint(models.Model):
    _inherit = "fastapi.endpoint"

    app: str = fields.Selection(
        selection_add=[(APP_NAME, "PMS Booking Engine API")],
        ondelete={APP_NAME: "cascade"},
    )

    def _get_fastapi_routers(self):
        if self.app == APP_NAME:
            return [booking_engine_router]
        return super()._get_fastapi_routers()

    def _get_app(self):
        app = super()._get_app()
        if self.app == APP_NAME:
            app.add_middleware(ProblemDetailsMiddleware)
            # Added last, so it wraps the problem responses too. Anonymous and
            # read only: the engine calls it from the browser on any hotel
            # website, without credentials.
            app.add_middleware(
                CORSMiddleware,
                allow_origins=["*"],
                allow_methods=["GET"],
                allow_headers=["*"],
            )
            app.openapi = self._get_problem_openapi(app)
        return app

    def _get_problem_openapi(self, app):
        """OpenAPI of the app with the error bodies documented as
        application/problem+json, the media type they are sent with. FastAPI
        documents every response model as application/json."""

        def openapi():
            if not app.openapi_schema:
                schema = FastAPI.openapi(app)
                for path in schema.get("paths", {}).values():
                    for operation in path.values():
                        for status, response in operation.get("responses", {}).items():
                            content = response.get("content", {})
                            if status.startswith(("4", "5")) and "application/json" in (
                                content
                            ):
                                response["content"] = {
                                    "application/problem+json": content[
                                        "application/json"
                                    ]
                                }
                app.openapi_schema = schema
            return app.openapi_schema

        return openapi


booking_engine_router = APIRouter(tags=["booking-engine"])
