from fastapi import APIRouter

from fastapi.middleware.cors import CORSMiddleware
from odoo import api, fields, models

APP_NAME = "pms_api"


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
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

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
