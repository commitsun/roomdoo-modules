import logging
from typing import Annotated

from fastapi import Depends, HTTPException

from odoo import models
from odoo.api import Environment

from odoo.addons.fastapi_auth_jwt.dependencies import AuthJwtOdooEnv
from odoo.addons.pms_bookai.schemas.bookai_template import BookaiTemplateAvailability
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router

_logger = logging.getLogger(__name__)


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

    user_lang = helper._get_active_lang_code()
    user_tz = env.user.tz or "UTC"
    template_rows = templates.sudo().with_context(lang=user_lang, tz=user_tz)
    folio_ctx = (
        folio.sudo().with_context(lang=user_lang, tz=user_tz) if folio else False
    )

    rows = []
    for template in template_rows:
        if folio_ctx:
            try:
                body_rendered, params, _lang, _tz = template._bookai_render_body(
                    folio_ctx
                )
            except Exception:
                _logger.exception(
                    "Skipping BookAI template %s: render against folio %s failed",
                    template.bookai_template_code,
                    folio_ctx.id,
                )
                continue
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

    def _get_active_lang_code(self):
        Lang = self.env["res.lang"].sudo()

        for candidate in (self.env.context.get("lang"), self.env.user.lang):
            if not candidate:
                continue
            lang_rec = Lang.search(
                [
                    ("active", "=", True),
                    "|",
                    ("code", "=", candidate),
                    ("iso_code", "=", candidate),
                ],
                limit=1,
            )
            if lang_rec:
                return lang_rec.code or ""

        fallback_lang = Lang.search([("active", "=", True)], limit=1)
        return fallback_lang.code or ""

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
        # Folio-level picker can only render folio-model templates: a
        # per-reservation template (e.g. smartlock codes) evaluates expressions
        # like ``object.folio_id`` / ``object.preferred_room_id`` that do not
        # exist on a ``pms.folio`` and would raise while rendering, breaking the
        # whole list. Such templates are delivered through their own
        # per-reservation path, not from this folio picker.
        templates = templates.filtered(
            lambda tpl: tpl.model_id.model in ("pms.folio", False)
            and tpl._is_applicable_to_folio(folio)
        )
        return templates, folio
