import os
import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.middleware.cors import CORSMiddleware
from pyinstrument import Profiler

from odoo import api, fields, models

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

        # Guardar archivo
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
        return params


pms_api_router = APIRouter()
