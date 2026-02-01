from typing import Annotated

from fastapi import Depends, HTTPException

from odoo import models
from odoo.api import Environment

from odoo.addons.fastapi_auth_jwt.dependencies import AuthJwtOdooEnv
from odoo.addons.pms_bookai.schemas.bookai_template import BookaiTemplateAvailability
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router


@pms_api_router.get(
    "/bookai/templates/available",
    response_model=list[BookaiTemplateAvailability],
    tags=["bookai"],
)
async def list_available_bookai_templates(
    env: Annotated[Environment, Depends(AuthJwtOdooEnv(validator_name="api_pms"))],
    property_id: int,
    folio_id: int | None = None,
) -> list[BookaiTemplateAvailability]:
    """
    Return BookAI templates available for a property.

    If folio_id is provided:
    - templates are additionally filtered by apply_domain
    - parameter values and body_rendered are returned.
    """
    helper = env["pms_api_bookai.template_router.helper"].new()
    templates, folio = helper.get_templates_for_property_and_folio(
        property_id=property_id,
        folio_id=folio_id,
    )

    user_lang = env.user.lang or "en_US"
    user_tz = env.user.tz or "UTC"
    template_rows = templates.sudo().with_context(lang=user_lang, tz=user_tz)
    folio_ctx = (
        folio.sudo().with_context(lang=user_lang, tz=user_tz) if folio else False
    )

    rows = []
    for template in template_rows:
        if folio_ctx:
            body_rendered, params, _lang, _tz = template._bookai_render_body(folio_ctx)
            rows.append(
                BookaiTemplateAvailability.from_notification_template(
                    template,
                    param_values=params,
                    body_rendered=body_rendered,
                )
            )
        else:
            rows.append(BookaiTemplateAvailability.from_notification_template(template))
    return rows


class PmsApiBookaiTemplateRouterHelper(models.AbstractModel):
    _name = "pms_api_bookai.template_router.helper"
    _description = "PMS API BookAI Template Helper"

    def _get_allowed_property(self, property_id: int):
        pms_property = (
            self.env["pms.property"]
            .sudo()
            .search(
                [("id", "=", property_id), ("user_ids", "in", [self.env.user.id])],
                limit=1,
            )
        )
        if not pms_property:
            raise HTTPException(status_code=404, detail="property not found")
        return pms_property

    def _get_allowed_folio(self, folio_id: int):
        folio = self.env["pms.folio"].sudo().browse(folio_id).exists()
        if not folio:
            raise HTTPException(status_code=404, detail="folio not found")
        self._get_allowed_property(folio.pms_property_id.id)
        return folio

    def get_templates_for_property(self, property_id: int):
        pms_property = self._get_allowed_property(property_id)
        Template = self.env["pms.notification.template"].sudo()
        domain = [
            ("bookai_template_code", "!=", False),
            ("active", "=", True),
        ] + Template._property_availability_domain(pms_property.id)
        return Template.search(domain, order="name")

    def get_templates_for_property_and_folio(
        self,
        property_id: int,
        folio_id: int | None = None,
    ):
        pms_property = self._get_allowed_property(property_id)
        templates = self.get_templates_for_property(property_id)
        if not folio_id:
            return templates, False

        folio = self._get_allowed_folio(folio_id)
        if folio.pms_property_id.id != pms_property.id:
            raise HTTPException(
                status_code=400,
                detail="folio does not belong to requested property",
            )
        templates = templates.filtered(lambda tpl: tpl._is_applicable_to_folio(folio))
        return templates, folio
