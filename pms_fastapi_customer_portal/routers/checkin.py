from fastapi.responses import Response

from odoo import _, models

from odoo.addons.pms_fastapi.dependencies import PublicEnv
from odoo.addons.pms_fastapi.routers.contact_id_number import (
    list_id_number_categories,
)
from odoo.addons.pms_fastapi.routers.country import get_server_countries
from odoo.addons.pms_fastapi.routers.country_state import get_country_states
from odoo.addons.pms_fastapi.schemas.checkin import (
    CheckinContext,
    CheckinGuests,
    CheckinPartnerDetail,
    CheckinPartnerUpdate,
)
from odoo.addons.pms_fastapi.schemas.contact_id_number import (
    ContactIdNumberCategorySummary,
)
from odoo.addons.pms_fastapi.schemas.country import CountrySummary
from odoo.addons.pms_fastapi.schemas.country_state import CountryStateSummary
from odoo.addons.pms_fastapi.utils import ApiProblem
from odoo.addons.pms_fastapi_customer_portal.models.fastapi_endpoint import (
    customer_portal_router,
)

from ..models.pms_checkin_partner import PORTAL_CHECKIN_CONTEXT
from .portal import HELPER as PORTAL_HELPER

CHECKIN_HELPER = "pms_fastapi_customer_portal.checkin_router.helper"
# Folio states whose guests may be checked in from the portal.
CHECKIN_FOLIO_STATES = ("confirm",)

# The check-in routes of the staff API, with the folio of the token in place of
# the folio id. Every problem of the check-in helper, its own and the staff
# ones, is answered as problem+json.


@customer_portal_router.get(
    "/portal/{token}/checkin-guests", response_model_exclude_unset=True
)
async def list_portal_checkin_guests(env: PublicEnv, token: str) -> CheckinGuests:
    """Guests of the folio grouped by reservation (room)."""
    try:
        return env[CHECKIN_HELPER].new().get_guests(token)
    except ApiProblem as problem:
        return problem.response


@customer_portal_router.get(
    "/portal/{token}/checkin-context", response_model_exclude_unset=True
)
async def get_portal_checkin_context(env: PublicEnv, token: str) -> CheckinContext:
    """Property and folio data the check-in wizard needs."""
    try:
        return env[CHECKIN_HELPER].new().get_context(token)
    except ApiProblem as problem:
        return problem.response


@customer_portal_router.get(
    "/portal/{token}/checkin-partners/{checkin_partner_id}",
    response_model_exclude_unset=True,
)
async def get_portal_checkin_partner(
    env: PublicEnv, token: str, checkin_partner_id: int
) -> CheckinPartnerDetail:
    """Stored data of a guest. Empty fields are omitted."""
    try:
        return env[CHECKIN_HELPER].new().get_partner_detail(token, checkin_partner_id)
    except ApiProblem as problem:
        return problem.response


@customer_portal_router.patch(
    "/portal/{token}/checkin-partners/{checkin_partner_id}",
    status_code=204,
    response_class=Response,
)
async def update_portal_checkin_partner(
    env: PublicEnv,
    token: str,
    checkin_partner_id: int,
    payload: CheckinPartnerUpdate,
) -> Response:
    """Update a guest with merge-patch semantics.

    An absent field is left untouched and a null one is cleared. Only data is
    written: the guest is never boarded."""
    try:
        env[CHECKIN_HELPER].new().update_partner(token, checkin_partner_id, payload)
    except ApiProblem as problem:
        return problem.response
    return Response(status_code=204)


# The catalogs the check-in refers to by id, as the staff API serves them.


@customer_portal_router.get("/portal/{token}/countries")
async def list_portal_countries(env: PublicEnv, token: str) -> list[CountrySummary]:
    try:
        env[CHECKIN_HELPER].new().check_token(token)
    except ApiProblem as problem:
        return problem.response
    return await get_server_countries(env)


@customer_portal_router.get("/portal/{token}/country-states")
async def list_portal_country_states(
    env: PublicEnv, token: str, country: int | None = None
) -> list[CountryStateSummary]:
    try:
        env[CHECKIN_HELPER].new().check_token(token)
    except ApiProblem as problem:
        return problem.response
    return await get_country_states(env, country)


@customer_portal_router.get("/portal/{token}/id-number-categories")
async def list_portal_id_number_categories(
    env: PublicEnv, token: str, country: int | None = None
) -> list[ContactIdNumberCategorySummary]:
    try:
        env[CHECKIN_HELPER].new().check_token(token)
    except ApiProblem as problem:
        return problem.response
    return await list_id_number_categories(env, country)


class CustomerPortalCheckinRouterHelper(models.AbstractModel):
    """The staff check-in helper, scoped by the folio of a portal token.

    Every public method of the staff helper takes the token in place of the
    folio id, and reads and writes as the staff one does.
    """

    _name = CHECKIN_HELPER
    _inherit = "pms_api_checkin.checkin_router.helper"
    _description = "Customer portal check-in API router helper"

    # Scope

    def _get_folio(self, token):
        """The folio of the token, read with sudo, while it is confirmed.

        The other folios answer as an unknown token does, so link holders
        cannot read the guests of past or cancelled stays.
        """
        portal_helper = self.env[PORTAL_HELPER].new()
        folio = portal_helper._get_folio(token)
        if folio.state not in CHECKIN_FOLIO_STATES:
            raise portal_helper._link_not_found()
        return folio

    def check_token(self, token):
        """Raise the problem of an unknown token unless it opens a check-in."""
        self._get_folio(token)

    def _get_checkin_partner_or_404(self, folio, checkin_partner_id):
        """A guest of a live stay of the folio, as a problem+json 404 otherwise.

        The guests of a cancelled reservation are not listed, so they are not
        found either.
        """
        guest = folio.checkin_partner_ids.filtered(
            lambda cp: (
                cp.id == checkin_partner_id and cp.reservation_id.state != "cancel"
            )
        )
        if not guest:
            self._raise_problem(
                404,
                "/errors/guest-not-found",
                _("Guest not found"),
                _("The guest is not part of this booking."),
            )
        return guest

    # Update

    def update_partner(self, token, checkin_partner_id, payload):
        """Write the guest as the staff check-in does, as a portal write.

        The portal context keeps the guest from being linked to the contact
        that holds its document number."""
        helper = self.with_context(**{PORTAL_CHECKIN_CONTEXT: True})
        return super(CustomerPortalCheckinRouterHelper, helper).update_partner(
            token, checkin_partner_id, payload
        )
