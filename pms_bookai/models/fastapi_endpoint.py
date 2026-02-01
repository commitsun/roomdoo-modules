from odoo import models

from odoo.addons.pms_fastapi.models.fastapi_endpoint import APP_NAME


class FastapiEndpoint(models.Model):
    _inherit = "fastapi.endpoint"

    def _prepare_fastapi_app_params(self):  # noqa: D102
        params = super()._prepare_fastapi_app_params()
        if self.app == APP_NAME:
            tags_metadata = params.get("openapi_tags", []) or []
            if not any(tag.get("name") == "bookai" for tag in tags_metadata):
                tags_metadata.append(
                    {
                        "name": "bookai",
                        "description": "BookAI WhatsApp template operations",
                    }
                )
            params["openapi_tags"] = tags_metadata
        return params
