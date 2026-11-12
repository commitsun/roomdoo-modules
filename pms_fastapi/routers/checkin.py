import base64
import binascii
from datetime import date

from fastapi import HTTPException
from fastapi.responses import Response

from odoo import _, models
from odoo.exceptions import AccessDenied, AccessError, UserError

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
    CheckinPartnerUpdate,
    CheckinRoom,
)
from odoo.addons.pms_fastapi.utils import ApiProblem, build_problem

ORIGIN_INPUT_WIZARD = "wizard"
# Payload key -> check-in partner field, for the values written as they come.
SIMPLE_FIELDS = {
    "firstname": "firstname",
    "lastname": "lastname",
    "lastname2": "lastname2",
    "documentNumber": "document_number",
    "documentSupportNumber": "support_number",
    "residenceCity": "city",
    "residenceStreet": "street",
    "zip": "zip",
    "email": "email",
    "mobile": "mobile",
}
# Payload key -> check-in partner field, for the ISO country codes.
COUNTRY_FIELDS = {
    "nationality": "nationality_id",
    "documentCountryId": "document_country_id",
    "countryId": "country_id",
}


class _CheckinProblem(ApiProblem):
    """Check-in router problem, caught by this router's local handlers."""


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


@pms_api_router.patch(
    "/folios/{folio_id}/checkin-partners/{checkin_partner_id}",
    status_code=204,
    response_class=Response,
    tags=["checkin"],
)
async def update_checkin_partner(
    env: AuthenticatedEnv,
    folio_id: int,
    checkin_partner_id: int,
    payload: CheckinPartnerUpdate,
) -> Response:
    """Update a guest with merge-patch semantics.

    An absent field is left untouched and a null one is cleared. Only data is
    written: the guest is never boarded."""
    helper = env["pms_api_checkin.checkin_router.helper"].new()
    try:
        helper.update_partner(folio_id, checkin_partner_id, payload)
    except _CheckinProblem as problem:
        return problem.response
    return Response(status_code=204)


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

    @staticmethod
    def _raise_problem(status, type_, title, detail, **extra):
        raise _CheckinProblem(build_problem(status, type_, title, detail, **extra))

    # Value conversions

    @staticmethod
    def _country_code(country):
        return country.code or None

    def _country_from_code(self, code, field):
        country = self.env["res.country"].search([("code", "=ilike", code)], limit=1)
        if not country:
            self._raise_problem(
                422,
                "/errors/unknown-country",
                _("Unknown country"),
                _("The country code is not valid."),
                field=field,
            )
        return country

    def _document_type_from_code(self, code):
        category = self.env["res.partner.id_category"].search(
            [("code", "=", code)], limit=1
        )
        if not category:
            self._raise_problem(
                422,
                "/errors/unknown-document-type",
                _("Unknown document type"),
                _("The document type code is not valid."),
                field="documentType",
            )
        return category

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

    # Update

    def update_partner(self, folio_id, checkin_partner_id, payload):
        folio = self._get_folio(folio_id)
        cp = self._get_checkin_partner_or_404(folio, checkin_partner_id)
        data = payload.model_dump(exclude_unset=True)
        if not data:
            return
        vals = self._prepare_update_vals(folio, cp, data)
        vals["origin_input_data"] = ORIGIN_INPUT_WIZARD
        try:
            with self.env.cr.savepoint():
                cp.write(vals)
        except UserError as err:
            self._raise_problem(
                422,
                "/errors/checkin-partner-invalid",
                _("Check-in data not valid"),
                err.args[0] if err.args else str(err),
            )

    def _prepare_update_vals(self, folio, cp, data):
        vals = {}
        for key, odoo_field in SIMPLE_FIELDS.items():
            if key in data:
                vals[odoo_field] = data[key] or False
        if "gender" in data:
            gender = data["gender"]
            vals["gender"] = getattr(gender, "value", gender) or False
        if "birthdate" in data:
            vals["birthdate_date"] = data["birthdate"] or False
        for key, odoo_field in COUNTRY_FIELDS.items():
            if key in data:
                code = data[key]
                vals[odoo_field] = (
                    self._country_from_code(code, key).id if code else False
                )
        if "documentType" in data:
            code = data["documentType"]
            vals["document_type"] = (
                self._document_type_from_code(code).id if code else False
            )
        self._prepare_state_vals(cp, data, vals)
        self._prepare_relationship_vals(folio, cp, data, vals)
        if "signature" in data:
            vals["signature"] = self._decode_signature(data["signature"])
        return vals

    def _prepare_state_vals(self, cp, data, vals):
        country_id = vals.get("country_id", cp.country_id.id)
        country = self.env["res.country"].browse(country_id)
        if "countryState" in data:
            vals["state_id"] = self._state_from_name(data["countryState"], country).id
        elif (
            "country_id" in vals
            and cp.state_id
            and cp.state_id.country_id != country
            and country != self.env.ref("base.es")
        ):
            # The residence country changed and the stored state no longer
            # belongs to it.
            vals["state_id"] = False

    def _state_from_name(self, name, country):
        State = self.env["res.country.state"]
        if not name:
            return State
        domain = [("country_id", "=", country.id)] if country else []
        state = State.search(domain + [("name", "=ilike", name)], limit=1)
        if not state:
            state = State.search(domain + [("code", "=ilike", name)], limit=1)
        if state:
            return state
        # A state unknown for a non-Spanish country is dropped, as in
        # pms_api_rest (_get_residence_state_id); Spanish residents need it.
        if country and country != self.env.ref("base.es"):
            return State
        self._raise_problem(
            422,
            "/errors/unknown-country-state",
            _("Unknown state"),
            _("The state is not valid for the residence country."),
            field="countryState",
        )

    def _prepare_relationship_vals(self, folio, cp, data, vals):
        if "relationship" in data:
            relationship = data["relationship"]
            valid_codes = dict(cp._fields["ses_partners_relationship"].selection)
            if relationship and relationship not in valid_codes:
                self._raise_problem(
                    422,
                    "/errors/unknown-relationship",
                    _("Unknown relationship"),
                    _("The relationship code is not valid."),
                    field="relationship",
                )
            # Stored from the guest's own perspective, as pms_api_rest does.
            vals["ses_partners_relationship"] = (
                find_opposite_relationship(relationship) if relationship else False
            )
        if "responsibleCheckinPartnerId" in data:
            responsible = self._get_responsible(
                folio, cp, data["responsibleCheckinPartnerId"]
            )
            vals["ses_related_checkin_partner_id"] = responsible.id or False

    def _get_responsible(self, folio, cp, responsible_id):
        if not responsible_id:
            return self.env["pms.checkin.partner"]
        responsible = folio.checkin_partner_ids.filtered(
            lambda c: str(c.id) == str(responsible_id) and c != cp
        )
        if not responsible:
            self._raise_problem(
                422,
                "/errors/unknown-responsible",
                _("Unknown responsible"),
                _("The responsible must be another guest of the same folio."),
                field="responsibleCheckinPartnerId",
            )
        return responsible

    def _decode_signature(self, signature):
        if not signature:
            return False
        try:
            raw = base64.b64decode(signature, validate=True)
        except (binascii.Error, ValueError):
            raw = b""
        if not raw:
            self._raise_problem(
                422,
                "/errors/invalid-signature",
                _("Invalid signature"),
                _("The signature is not valid base64."),
                field="signature",
            )
        return base64.b64encode(raw)
