import functools
import os
import time
from pathlib import Path
from typing import Annotated, get_args, get_origin, get_type_hints

from fastapi import APIRouter, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from odoo import api, fields, models

from ..schemas.base import MIN_SEARCH_TEXT_LENGTH, BaseSearch

APP_NAME = "pms_api"


class ProfilerMiddleware:
    def __init__(self, app, enable_by_param: bool = True):
        self.app = app
        self.enable_by_param = enable_by_param
        self.output_dir = os.getenv("PROFILE_OUTPUT_DIR", "/tmp/profiles")
        try:
            Path(self.output_dir).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"Cannot create profile directory {self.output_dir}: {e}")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        request = Request(scope, receive)

        should_profile = (
            self.enable_by_param and request.query_params.get("__profile__") == "1"
        )

        if not should_profile:
            return await self.app(scope, receive, send)

        from pyinstrument import Profiler

        profiler = Profiler(interval=0.0001)
        profiler.start()

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = message.get("headers", [])
                headers.append((b"x-profiled", b"true"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)

        profiler.stop()

        timestamp = int(time.time())
        path = request.url.path.replace("/", "_")
        filename = f"{self.output_dir}/profile{path}_{timestamp}.html"

        try:
            with open(filename, "w") as f:
                f.write(profiler.output_html())
            print(f"Profile saved: {filename}")
        except Exception as e:
            print(f"Error saving profile: {e}")


class FastapiEndpoint(models.Model):
    _inherit = "fastapi.endpoint"

    app: str = fields.Selection(
        selection_add=[(APP_NAME, "PMS API")],
        ondelete={APP_NAME: "cascade"},
    )

    @api.model
    def _get_fastapi_routers(self):
        if self.app == APP_NAME:
            return [pms_api_router]
        return super()._get_fastapi_routers()

    def _get_app(self):
        app = super()._get_app()
        if self.app == APP_NAME:
            # modify temporarily CORS middleware for PMS FastAPI app until
            # pms_api_rest is removed.
            # app_url = (
            #     self.env["ir.config_parameter"]
            #     .sudo()
            #     .get_param("roomdoo_app_url", default="*")
            # )
            # app.add_middleware(
            #     CORSMiddleware,
            #     allow_origins=[app_url],
            #     allow_credentials=True,
            #     allow_methods=["*"],
            #     allow_headers=["*"],
            #     expose_headers=["set-cookie"],
            # )
            app.add_middleware(
                CORSMiddleware,
                allow_origins=["*"],
                allow_methods=["*"],
                allow_headers=["*"],
            )
            if os.getenv("ENABLE_PROFILER", "0") == "1":
                app.add_middleware(ProfilerMiddleware)
        return app

    def _prepare_fastapi_app_params(self):  # noqa: D102
        params = super()._prepare_fastapi_app_params()
        if self.app == APP_NAME:
            tags_metadata = params.get("openapi_tags", []) or []
            tags_metadata.append(
                {
                    "name": "login",
                    "description": "Login operation, used to get a JWT.",
                }
            )
            tags_metadata.append(
                {
                    "name": "db_info",
                    "description": "Database information operations",
                }
            )
            tags_metadata.append(
                {
                    "name": "property",
                    "description": "Properties related operations",
                }
            )

            params["openapi_tags"] = tags_metadata
            params["strict_content_type"] = False
        return params


def _search_text_too_short_problem(field: str) -> JSONResponse:
    """RFC 9457 problem+json for a free-text search value below the minimum length."""
    return JSONResponse(
        status_code=400,
        media_type="application/problem+json",
        content={
            "type": "/errors/search-text-too-short",
            "title": "Search text too short",
            "status": 400,
            "detail": (
                f"Search text must be at least {MIN_SEARCH_TEXT_LENGTH} characters."
            ),
            "field": field,
            "minLength": MIN_SEARCH_TEXT_LENGTH,
        },
    )


def _endpoint_search_param(endpoint) -> str | None:
    """Name of the endpoint param typed as a BaseSearch subclass, if any."""
    for name, hint in get_type_hints(endpoint, include_extras=True).items():
        annotated = get_args(hint)[0] if get_origin(hint) is Annotated else hint
        if isinstance(annotated, type) and issubclass(annotated, BaseSearch):
            return name
    return None


def _guard_search_text(endpoint):
    """Wrap an endpoint so its BaseSearch filter is checked for too-short text.

    Returns the endpoint unchanged when it has no BaseSearch param. The wrapper
    keeps the original signature (functools.wraps) so FastAPI still introspects
    the real params.
    """
    search_param = _endpoint_search_param(endpoint)
    if search_param is None:
        return endpoint

    @functools.wraps(endpoint)
    async def wrapper(*args, **kwargs):
        filters = kwargs.get(search_param)
        if filters is not None:
            field = filters.first_short_search_text()
            if field is not None:
                return _search_text_too_short_problem(field)
        return await endpoint(*args, **kwargs)

    return wrapper


class PmsApiRouter(APIRouter):
    """APIRouter that auto-applies the free-text search length guard.

    Any endpoint with a BaseSearch param is wrapped at registration time, so the
    guard needs no per-endpoint code and covers future search endpoints too.
    """

    def add_api_route(self, path, endpoint, **kwargs):
        return super().add_api_route(path, _guard_search_text(endpoint), **kwargs)


pms_api_router = PmsApiRouter()
