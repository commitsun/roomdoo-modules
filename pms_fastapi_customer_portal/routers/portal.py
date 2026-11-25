from odoo import _, models

from odoo.addons.pms_fastapi.dependencies import PublicEnv
from odoo.addons.pms_fastapi.utils import ApiProblem, build_problem
from odoo.addons.pms_fastapi_customer_portal.models.fastapi_endpoint import (
    customer_portal_router,
)
from odoo.addons.pms_fastapi_customer_portal.schemas.portal_folio import (
    FOLIO_STATES,
    PortalFolio,
    PortalSaleLine,
)

HELPER = "pms_fastapi_customer_portal.portal_router.helper"


class _PortalProblem(ApiProblem):
    """Customer portal problem, caught by this router's handlers."""


@customer_portal_router.get("/portal/{token}/folio")
async def get_portal_folio(env: PublicEnv, token: str) -> PortalFolio:
    try:
        return env[HELPER].new().get_folio(token)
    except _PortalProblem as problem:
        return problem.response


class CustomerPortalRouterHelper(models.AbstractModel):
    _name = HELPER
    _description = "Customer portal API router helper"

    # Token

    def _portal_folio_domain(self, token):
        """Folios a portal link may open: confirmed or past, with a stay."""
        return [
            ("access_token", "=", token),
            ("state", "in", list(FOLIO_STATES)),
            ("reservation_ids", "!=", False),
        ]

    def _get_folio(self, token):
        """The folio of the token, read with sudo: the token is the access.

        An unknown token and a folio the portal may not open answer the same,
        so a token cannot be used to probe which folios exist.
        """
        folio = self.env["pms.folio"]
        if token:
            folio = folio.sudo().search(self._portal_folio_domain(token), limit=1)
        if not folio:
            raise _PortalProblem(
                build_problem(
                    404,
                    "/errors/portal-link-not-found",
                    _("Link not found"),
                    _("This link is not valid."),
                )
            )
        return folio

    # Folio

    def get_folio(self, token) -> PortalFolio:
        folio = self._get_folio(token)
        return PortalFolio.from_pms_folio(folio, self._get_sale_lines(folio))

    def _get_sale_lines(self, folio):
        """The folio sale lines aggregated for display, taxes included.

        Rooms are grouped by room type and the other services by product, each
        at its price before discount. The discounts of every line add up to
        one negative line, and the tourist tax goes last. The amounts add up
        to the folio total. Sections and down payments are left out.
        """
        currency = folio.currency_id
        rooms, services, tourist_taxes = {}, {}, {}
        discount = 0.0
        for line in folio.sale_line_ids:
            if line.display_type or line.is_downpayment:
                continue
            full_price = line.tax_ids.compute_all(
                line.price_unit,
                currency,
                line.product_uom_qty,
                product=line.product_id,
            )["total_included"]
            discount += line.price_total - full_price
            if line.reservation_id and not line.service_id:
                room_type = line.reservation_id.room_type_id
                group, key, name = rooms, room_type.id, room_type.name
            else:
                group = tourist_taxes if line.product_id.is_tourist_tax else services
                key, name = line.product_id.id, line.product_id.name or line.name
            group_name, amount = group.get(key, (name, 0.0))
            group[key] = (group_name, amount + full_price)
        lines = [*rooms.values(), *services.values()]
        if not currency.is_zero(discount):
            lines.append((_("Discounts"), discount))
        lines.extend(tourist_taxes.values())
        return [
            PortalSaleLine.from_amount(name, amount, currency) for name, amount in lines
        ]
