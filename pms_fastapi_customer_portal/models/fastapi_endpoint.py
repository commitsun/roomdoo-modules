from fastapi import APIRouter
from fastapi.middleware.cors import CORSMiddleware

from odoo import api, fields, models

APP_NAME = "customer_portal"
# Origin of the customer portal front, e.g. https://portal.example.com. When it
# is not set, any origin may call the API: the portal sends no credentials.
PORTAL_URL_PARAM = "roomdoo_customer_portal_url"


class FastapiEndpoint(models.Model):
    _inherit = "fastapi.endpoint"

    app: str = fields.Selection(
        selection_add=[(APP_NAME, "Customer Portal API")],
        ondelete={APP_NAME: "cascade"},
    )

    @api.model
    def _get_fastapi_routers(self):
        if self.app == APP_NAME:
            return [customer_portal_router]
        return super()._get_fastapi_routers()

    def _get_routing_info(self):
        res = super()._get_routing_info()
        if self.app == APP_NAME:
            # The portal is authorized by the token in the path alone: no
            # session is read, so none is saved or sent back as a cookie.
            res["save_session"] = False
        return res

    def _get_app(self):
        app = super()._get_app()
        if self.app == APP_NAME:
            portal_url = (
                self.env["ir.config_parameter"].sudo().get_param(PORTAL_URL_PARAM)
            )
            app.add_middleware(
                CORSMiddleware,
                allow_origins=[portal_url] if portal_url else ["*"],
                allow_methods=["GET", "PATCH", "POST"],
                allow_headers=["*"],
            )
        return app


customer_portal_router = APIRouter(tags=["customer portal"])
