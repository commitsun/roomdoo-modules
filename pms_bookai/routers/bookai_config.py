from odoo import models

from odoo.addons.pms_bookai.schemas.bookai_config import (
    BookaiConfig,
    BookaiPropertyConfig,
)
from odoo.addons.pms_fastapi.dependencies import AuthenticatedEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router


@pms_api_router.get(
    "/bookai/config",
    response_model=BookaiConfig,
    tags=["bookai"],
)
async def get_bookai_config(
    env: AuthenticatedEnv,
) -> BookaiConfig:
    """
    Return BookAI configuration for the current instance and
    the authenticated user's properties.
    """
    helper = env["pms_api_bookai.config_router.helper"].new()
    return helper.get_config()


class PmsApiBookaiConfigRouterHelper(models.AbstractModel):
    _name = "pms_api_bookai.config_router.helper"
    _description = "PMS API BookAI Config Helper"

    def _get_bookai_token(self):
        """Return the token for frontend-to-BookAI communication.

        Override this method to implement token exchange or JWT signing.
        Default: returns the instance token directly.
        """
        icp = self.env["ir.config_parameter"].sudo()
        return icp.get_param("pms_bookai.api_token", "")

    def get_config(self):
        icp = self.env["ir.config_parameter"].sudo()
        base_url = icp.get_param("pms_bookai.api_endpoint", "")
        token = self._get_bookai_token()
        bookai_enabled = bool(base_url and token)
        fallback_app_url = icp.get_param("roomdoo_app_url", "")

        properties = (
            self.env["pms.property"]
            .sudo()
            .search([("user_ids", "in", [self.env.user.id])])
        )

        return BookaiConfig(
            bookaiEnabled=bookai_enabled,
            bookaiBaseUrl=base_url if bookai_enabled else "",
            bookaiToken=token if bookai_enabled else "",
            properties=[
                BookaiPropertyConfig.from_pms_property(prop, fallback_app_url)
                for prop in properties
            ],
        )
