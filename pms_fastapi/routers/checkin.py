from datetime import date

from fastapi import HTTPException

from odoo import _, models
from odoo.exceptions import AccessDenied, AccessError

from odoo.addons.pms_api_rest.services.pms_reservation_service import (
    find_opposite_relationship,
)
from odoo.addons.pms_fastapi.dependencies import AuthenticatedEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.checkin import (
    CheckinContext,
    CheckinGuest,
    CheckinGuests,
    CheckinPartnerDetail,
    CheckinRoom,
)


@pms_api_router.get(
    "/folios/{folio_id}/checkin-guests",
    response_model_exclude_unset=True,
    tags=["checkin"],
)
async def list_checkin_guests(env: AuthenticatedEnv, folio_id: int) -> CheckinGuests:
    """Guests of the folio grouped by reservation (room)."""
    return env["pms_api_checkin.checkin_router.helper"].new().get_guests(folio_id)


@pms_api_router.get(
    "/folios/{folio_id}/checkin-context",
    response_model_exclude_unset=True,
    tags=["checkin"],
)
async def get_checkin_context(env: AuthenticatedEnv, folio_id: int) -> CheckinContext:
    """Property and folio data the check-in wizard needs."""
    return env["pms_api_checkin.checkin_router.helper"].new().get_context(folio_id)


@pms_api_router.get(
    "/folios/{folio_id}/checkin-partners/{checkin_partner_id}",
    response_model_exclude_unset=True,
    tags=["checkin"],
)
async def get_checkin_partner(
    env: AuthenticatedEnv, folio_id: int, checkin_partner_id: int
) -> CheckinPartnerDetail:
    """Stored data of a guest. Empty fields are omitted."""
    return (
        env["pms_api_checkin.checkin_router.helper"]
        .new()
        .get_partner_detail(folio_id, checkin_partner_id)
    )


class PmsApiCheckinRouterHelper(models.AbstractModel):
    _name = "pms_api_checkin.checkin_router.helper"
    _description = "Pms api check-in Service Helper"

    # Scope

    def _get_folio(self, folio_id):
        try:
            return (
                self.env["pms_api_folio.folio_router.helper"].new().get_or_404(folio_id)
            )
        except (AccessError, AccessDenied) as err:
            # A folio of a property the user cannot access is reported as not
            # found, as the company scope of the folio router already does.
            raise HTTPException(
                status_code=404,
                detail=_("folio not found"),
            ) from err

    def _get_checkin_partner_or_404(self, folio, checkin_partner_id):
        checkin_partner = folio.checkin_partner_ids.filtered(
            lambda cp: cp.id == checkin_partner_id
        )
        if not checkin_partner:
            raise HTTPException(
                status_code=404,
                detail=_("checkin partner not found"),
            )
        return checkin_partner

    # Value conversions

    @staticmethod
    def _country_code(country):
        return country.code or None

    # Guests list

    def get_guests(self, folio_id) -> CheckinGuests:
        folio = self._get_folio(folio_id)
        rooms = []
        reservations = folio.reservation_ids.sorted(
            lambda r: (r.checkin or date.min, r.id)
        )
        for reservation in reservations:
            guests = [
                CheckinGuest(
                    id=str(cp.id),
                    state=cp.state,
                    displayName=cp.name or None,
                    birthdate=cp.birthdate_date or None,
                )
                for cp in reservation.checkin_partner_ids.sorted("id")
            ]
            if not guests:
                continue
            room = reservation.preferred_room_id
            rooms.append(
                CheckinRoom(
                    id=str(reservation.id),
                    name=room.name
                    if room
                    else (reservation.room_type_id.name or reservation.name),
                    checkinPartners=guests,
                )
            )
        return CheckinGuests(rooms=rooms)

    # Guest detail

    def get_partner_detail(self, folio_id, checkin_partner_id) -> CheckinPartnerDetail:
        folio = self._get_folio(folio_id)
        cp = self._get_checkin_partner_or_404(folio, checkin_partner_id)
        values = {
            "firstname": cp.firstname,
            "lastname": cp.lastname,
            "lastname2": cp.lastname2,
            "gender": cp.gender,
            "birthdate": cp.birthdate_date,
            "nationality": self._country_code(cp.nationality_id),
            "documentType": cp.document_type.code,
            "documentNumber": cp.document_number,
            "documentCountryId": self._country_code(cp.document_country_id),
            "documentSupportNumber": cp.support_number,
            "responsibleCheckinPartnerId": str(cp.ses_related_checkin_partner_id.id)
            if cp.ses_related_checkin_partner_id
            else None,
            # Stored from the guest's own perspective, exchanged from the
            # responsible adult's one, as pms_api_rest does.
            "relationship": find_opposite_relationship(cp.ses_partners_relationship)
            if cp.ses_partners_relationship
            else None,
            "countryId": self._country_code(cp.country_id),
            "countryState": cp.state_id.name,
            "residenceCity": cp.city,
            "residenceStreet": cp.street,
            "zip": cp.zip,
            "email": cp.email,
            "mobile": cp.mobile,
        }
        return CheckinPartnerDetail(**{k: v for k, v in values.items() if v})

    # Context

    def get_context(self, folio_id) -> CheckinContext:
        folio = self._get_folio(folio_id)
        pms_property = folio.pms_property_id
        brand = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("roomdoo_fastapi.instance_name", default="Roomdoo")
        )
        values = {
            "hotelName": pms_property.name,
            "arrivalDate": folio.first_checkin or None,
            "unaccompaniedMinors": bool(folio.ses_unaccompanied_minors),
            "ocrAvailable": bool(pms_property.ocr_checkin_supplier),
            "brandName": brand or None,
        }
        values = {k: v for k, v in values.items() if v is not None}
        country = pms_property.country_id
        values["localization"] = country.code.lower() if country.code else None
        return CheckinContext(**values)
