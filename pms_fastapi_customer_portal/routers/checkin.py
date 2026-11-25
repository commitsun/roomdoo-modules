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
    CheckinDocumentData,
    CheckinGuests,
    CheckinPartnerDetail,
    CheckinPartnerUpdate,
    DocumentScanInput,
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
# Document scans a guest may ask for from the portal.
MAX_PORTAL_DOCUMENT_SCANS = 5

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


@customer_portal_router.post(
    "/portal/{token}/checkin-partners/{checkin_partner_id}/document-scan",
    response_model_exclude_unset=True,
)
async def scan_portal_checkin_partner_document(
    env: PublicEnv,
    token: str,
    checkin_partner_id: int,
    payload: DocumentScanInput,
) -> CheckinDocumentData:
    """Read an identity document with the property OCR. Nothing is saved but
    the count of scans of the guest, which has a limit."""
    try:
        return (
            env[CHECKIN_HELPER].new().scan_document(token, checkin_partner_id, payload)
        )
    except ApiProblem as problem:
        return problem.response


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

    # Document scan

    def scan_document(self, token, checkin_partner_id, payload):
        """Scan as the staff check-in does, within the scan limit of the guest.

        Every scan asked for a guest that may be modified counts, read or not,
        and the count is kept when the scan answers a problem.
        """
        folio = self._get_folio(token)
        cp = self._get_checkin_partner_or_404(folio, checkin_partner_id)
        self._check_not_read_only(cp)
        self._count_document_scan(cp)
        return super().scan_document(token, checkin_partner_id, payload)

    def _count_document_scan(self, cp):
        """Count a scan of the guest, or answer 429 when it has no scans left.

        The count is written with SQL: the ORM write of a guest would also run
        its partner and tourist tax side effects, the partner one without the
        portal context. The update locks the guest until the request ends, so
        concurrent scans of a guest are counted one after the other.
        """
        cp.flush_recordset(["portal_document_scan_count"])
        self.env.cr.execute(
            """
            UPDATE pms_checkin_partner
            SET portal_document_scan_count =
                COALESCE(portal_document_scan_count, 0) + 1
            WHERE id = %s AND COALESCE(portal_document_scan_count, 0) < %s
            RETURNING id
            """,
            (cp.id, MAX_PORTAL_DOCUMENT_SCANS),
        )
        counted = self.env.cr.fetchone()
        cp.invalidate_recordset(["portal_document_scan_count"])
        if not counted:
            self._raise_problem(
                429,
                "/errors/document-scan-limit",
                _("Document scan limit reached"),
                _("No more documents can be scanned for this guest."),
                maxScans=MAX_PORTAL_DOCUMENT_SCANS,
            )

    def _scan_result_values(self, result):
        # The portal user may not read the document types a result refers to.
        return super(
            CustomerPortalCheckinRouterHelper, self.sudo()
        )._scan_result_values(result)
