import base64
import binascii
import logging
from datetime import date, datetime

from fastapi import HTTPException
from fastapi.responses import Response

from odoo import _, models
from odoo.exceptions import UserError

from odoo.addons.pms_fastapi.dependencies import AuthenticatedEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.checkin import (
    CheckinContext,
    CheckinDocumentData,
    CheckinGuest,
    CheckinGuests,
    CheckinGuestState,
    CheckinHotelAskedField,
    CheckinPartnerDetail,
    CheckinPartnerUpdate,
    CheckinRoom,
    DocumentScanInput,
)
from odoo.addons.pms_fastapi.schemas.contact_id_number import (
    ContactIdNumberCategoryId,
)
from odoo.addons.pms_fastapi.schemas.country import CountryId
from odoo.addons.pms_fastapi.schemas.country_state import CountryStateId
from odoo.addons.pms_fastapi.utils import ApiProblem, build_problem

_logger = logging.getLogger(__name__)

ORIGIN_INPUT_WIZARD = "wizard"
GENDERS = ("male", "female", "other")
# Stored state of a guest -> API state. A cancelled guest is not listed.
GUEST_STATES = {
    "dummy": CheckinGuestState.pending,
    "draft": CheckinGuestState.pending,
    "precheckin": CheckinGuestState.completed,
    "onboard": CheckinGuestState.inHouse,
    "done": CheckinGuestState.checkedOut,
}
MAX_DOCUMENT_IMAGE_LENGTH = 1_500_000
MAX_SIGNATURE_LENGTH = 500_000
# Payload key -> check-in partner field, for the values written as they come.
SIMPLE_FIELDS = {
    "firstname": "firstname",
    "lastname": "lastname",
    "lastname2": "lastname2",
    "documentNumber": "document_number",
    "city": "city",
    "street": "street",
    "zipCode": "zip",
    "email": "email",
    "mobile": "mobile",
}
# Payload key -> check-in partner field, for the country ids.
COUNTRY_FIELDS = {
    "nationality": "nationality_id",
    "documentCountry": "document_country_id",
    "country": "country_id",
}
# Property option -> guest fields that the hotel asks when the option is on.
HOTEL_ASKED_FIELDS = {
    "checkin_ask_gender": (CheckinHotelAskedField.gender,),
    "checkin_ask_birthdate": (CheckinHotelAskedField.birthdate,),
    "checkin_ask_document": (
        CheckinHotelAskedField.documentType,
        CheckinHotelAskedField.documentNumber,
        CheckinHotelAskedField.documentCountry,
    ),
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


@pms_api_router.post(
    "/folios/{folio_id}/checkin-partners/{checkin_partner_id}/document-scan",
    tags=["checkin"],
    response_model_exclude_unset=True,
)
async def scan_checkin_partner_document(
    env: AuthenticatedEnv,
    folio_id: int,
    checkin_partner_id: int,
    payload: DocumentScanInput,
) -> CheckinDocumentData:
    """Read an identity document with the property OCR. Nothing is saved."""
    helper = env["pms_api_checkin.checkin_router.helper"].new()
    try:
        return helper.scan_document(folio_id, checkin_partner_id, payload)
    except _CheckinProblem as problem:
        return problem.response


class PmsApiCheckinRouterHelper(models.AbstractModel):
    _name = "pms_api_checkin.checkin_router.helper"
    _description = "Pms api check-in Service Helper"

    # Scope

    def _get_folio(self, folio_id):
        return self.env["pms_api_folio.folio_router.helper"].new().get_or_404(folio_id)

    def _get_checkin_partner_or_404(self, folio, checkin_partner_id):
        checkin_partner = folio.checkin_partner_ids.filtered(
            lambda cp: cp.id == checkin_partner_id
        )
        if not checkin_partner:
            raise HTTPException(
                status_code=404,
                detail=_("guest not found"),
            )
        return checkin_partner

    @staticmethod
    def _raise_problem(status, type_, title, detail, **extra):
        raise _CheckinProblem(build_problem(status, type_, title, detail, **extra))

    # Value conversions

    @staticmethod
    def _country_id(country):
        return CountryId.from_res_country(country) if country else None

    @staticmethod
    def _state_id(state):
        return CountryStateId.from_res_country_state(state) if state else None

    @staticmethod
    def _document_type_id(category):
        if not category:
            return None
        return ContactIdNumberCategoryId.from_res_partner_id_number_category(category)

    def _country_from_id(self, country_id, field):
        country = self.env["res.country"].browse(country_id).exists()
        if not country:
            self._raise_problem(
                422,
                "/errors/unknown-country",
                _("Unknown country"),
                _("The country is not valid."),
                field=field,
            )
        return country

    def _document_type_from_id(self, category_id):
        category = self.env["res.partner.id_category"].browse(category_id).exists()
        if not category:
            self._raise_problem(
                422,
                "/errors/unknown-document-type",
                _("Unknown document type"),
                _("The document type is not valid."),
                field="documentType",
            )
        return category

    def _state_from_id(self, state_id, country):
        state = self.env["res.country.state"].browse(state_id).exists()
        if not state or (country and state.country_id != country):
            self._raise_problem(
                422,
                "/errors/unknown-country-state",
                _("Unknown state"),
                _("The state is not valid for the residence country."),
                field="state",
            )
        return state

    @staticmethod
    def _date_iso(value):
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return str(value)[:10]

    @staticmethod
    def _present(values):
        return {k: v for k, v in values.items() if v not in (None, "")}

    # Guests list

    def get_guests(self, folio_id) -> CheckinGuests:
        folio = self._get_folio(folio_id)
        rooms = []
        reservations = folio.reservation_ids.filtered(
            lambda r: r.state != "cancel"
        ).sorted(lambda r: (r.checkin or date.min, r.id))
        for reservation in reservations:
            guests = [
                CheckinGuest(
                    id=cp.id,
                    state=GUEST_STATES[cp.state],
                    displayName=cp.name or None,
                    birthdate=cp.birthdate_date or None,
                )
                for cp in reservation.checkin_partner_ids.sorted("id")
                if cp.state in GUEST_STATES
            ]
            if not guests:
                continue
            room = reservation.preferred_room_id
            rooms.append(
                CheckinRoom(
                    id=reservation.id,
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
        return CheckinPartnerDetail(**self._present(self._partner_detail_values(cp)))

    def _partner_detail_values(self, cp):
        return {
            "firstname": cp.firstname or None,
            "lastname": cp.lastname or None,
            "lastname2": cp.lastname2 or None,
            "gender": cp.gender or None,
            "birthdate": cp.birthdate_date or None,
            "nationality": self._country_id(cp.nationality_id),
            "documentType": self._document_type_id(cp.document_type),
            "documentNumber": cp.document_number or None,
            "documentCountry": self._country_id(cp.document_country_id),
            "country": self._country_id(cp.country_id),
            "state": self._state_id(cp.state_id),
            "city": cp.city or None,
            "street": cp.street or None,
            "zipCode": cp.zip or None,
            "email": cp.email or None,
            "mobile": cp.mobile or None,
            "responsibleCheckinPartnerId": cp[self._responsible_field()].id or None,
        }

    def _responsible_field(self):
        """Check-in partner field that stores the responsible adult.

        A localization that keeps it in a field of its own overrides it."""
        return "responsible_checkin_partner_id"

    # Context

    def get_context(self, folio_id) -> CheckinContext:
        folio = self._get_folio(folio_id)
        values = self._context_values(folio)
        localization = values.pop("localization")
        return CheckinContext(localization=localization, **self._present(values))

    def _context_values(self, folio):
        pms_property = folio.pms_property_id
        country = pms_property.country_id
        return {
            "localization": country.code.lower() if country.code else None,
            "hotelName": pms_property.name,
            "arrivalDate": folio.first_checkin or None,
            "ocrAvailable": bool(pms_property.ocr_checkin_supplier),
            "hotelAskedFields": self._hotel_asked_fields(pms_property),
        }

    @staticmethod
    def _hotel_asked_fields(pms_property):
        return [
            field
            for option, fields in HOTEL_ASKED_FIELDS.items()
            if pms_property[option]
            for field in fields
        ]

    # Update

    def _check_not_read_only(self, cp):
        # The stored state of a guest is not recomputed when its reservation is
        # cancelled, so the reservation is checked as well
        if cp.state in ("done", "cancel") or cp.reservation_id.state == "cancel":
            self._raise_problem(
                409,
                "/errors/guest-read-only",
                _("Guest is read-only"),
                _(
                    "A guest that has left or whose reservation is cancelled "
                    "cannot be modified."
                ),
            )

    def update_partner(self, folio_id, checkin_partner_id, payload):
        folio = self._get_folio(folio_id)
        cp = self._get_checkin_partner_or_404(folio, checkin_partner_id)
        self._check_not_read_only(cp)
        data = payload.model_dump(mode="json", exclude_unset=True)
        if not data:
            return
        vals = self._prepare_update_vals(folio, cp, data)
        vals["origin_input_data"] = ORIGIN_INPUT_WIZARD
        try:
            with self.env.cr.savepoint():
                cp.write(vals)
        except UserError as err:
            _logger.warning(
                "Check-in data of guest %s rejected: %s",
                cp.id,
                err.args[0] if err.args else err,
            )
            self._raise_problem(
                422,
                "/errors/guest-invalid",
                _("Guest data not valid"),
                _("The guest data is not valid."),
            )

    def _prepare_update_vals(self, folio, cp, data):
        vals = {}
        for key, odoo_field in SIMPLE_FIELDS.items():
            if key in data:
                vals[odoo_field] = data[key] or False
        if "gender" in data:
            vals["gender"] = data["gender"] or False
        if "birthdate" in data:
            vals["birthdate_date"] = data["birthdate"] or False
        for key, odoo_field in COUNTRY_FIELDS.items():
            if key in data:
                country_id = data[key]
                vals[odoo_field] = (
                    False
                    if country_id is None
                    else self._country_from_id(country_id, key).id
                )
        if "documentType" in data:
            category_id = data["documentType"]
            vals["document_type"] = (
                False
                if category_id is None
                else self._document_type_from_id(category_id).id
            )
        self._prepare_state_vals(cp, data, vals)
        self._prepare_extra_vals(folio, cp, data, vals)
        if "responsibleCheckinPartnerId" in data:
            responsible = self._get_responsible(
                folio, cp, data["responsibleCheckinPartnerId"]
            )
            vals[self._responsible_field()] = responsible.id or False
        if "signature" in data:
            vals["signature"] = self._decode_signature(data["signature"])
        return vals

    def _get_responsible(self, folio, cp, responsible_id):
        if not responsible_id:
            return self.env["pms.checkin.partner"]
        responsible = folio.checkin_partner_ids.filtered(
            lambda c: c.id == responsible_id and c != cp
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

    def _prepare_state_vals(self, cp, data, vals):
        country_id = vals.get("country_id", cp.country_id.id)
        country = self.env["res.country"].browse(country_id)
        if "state" in data:
            state_id = data["state"]
            vals["state_id"] = (
                False if state_id is None else self._state_from_id(state_id, country).id
            )
        elif "country_id" in vals and self._state_is_stale(cp, country):
            # The residence country changed and the stored state no longer
            # belongs to it.
            vals["state_id"] = False

    def _state_is_stale(self, cp, country):
        return bool(cp.state_id and cp.state_id.country_id != country)

    def _prepare_extra_vals(self, folio, cp, data, vals):
        """Hook for the values that a localization adds to the payload."""

    def _check_image_size(self, image, field, max_length):
        if image and len(image) > max_length:
            self._raise_problem(
                422,
                "/errors/image-too-large",
                _("Image too large"),
                _("The image is too large."),
                field=field,
                maxLength=max_length,
            )

    def _decode_signature(self, signature):
        if not signature:
            return False
        self._check_image_size(signature, "signature", MAX_SIGNATURE_LENGTH)
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

    # Document scan

    def scan_document(self, folio_id, checkin_partner_id, payload):
        folio = self._get_folio(folio_id)
        cp = self._get_checkin_partner_or_404(folio, checkin_partner_id)
        self._check_not_read_only(cp)
        self._check_image_size(payload.front, "front", MAX_DOCUMENT_IMAGE_LENGTH)
        self._check_image_size(payload.back, "back", MAX_DOCUMENT_IMAGE_LENGTH)
        pms_property = folio.pms_property_id
        supplier = pms_property.ocr_checkin_supplier
        method_name = f"_{supplier}_document_process"
        if not supplier or not hasattr(pms_property, method_name):
            self._raise_problem(
                422,
                "/errors/ocr-not-available",
                _("Document scan not available"),
                _("The property has no document scanning service."),
            )
        try:
            # The supplier must not complete the result with the data of other
            # partners found by document number
            result = getattr(
                pms_property.with_context(ocr_skip_document_lookup=True), method_name
            )(payload.front, payload.back or False)
        except UserError:
            result = {}
        if not result:
            self._raise_problem(
                422,
                "/errors/document-not-readable",
                _("Document not readable"),
                _("The document could not be read."),
            )
        return CheckinDocumentData(**self._scan_result_values(result))

    def _scan_result_values(self, result):
        env = self.env
        values = {
            "firstname": result.get("firstname") or None,
            "lastname": result.get("lastname") or None,
            "lastname2": result.get("lastname2") or None,
            "documentNumber": result.get("document_number") or None,
            "street": result.get("residence_street") or None,
            "city": result.get("residence_city") or None,
            "zipCode": result.get("zip") or None,
        }
        for key, scan_key in (
            ("nationality", "nationality"),
            ("country", "country_id"),
            ("documentCountry", "document_country_id"),
        ):
            if result.get(scan_key):
                country = env["res.country"].browse(result[scan_key]).exists()
                values[key] = self._country_id(country)
        if result.get("document_type"):
            category = env["res.partner.id_category"].browse(result["document_type"])
            values["documentType"] = self._document_type_id(category.exists())
        if result.get("country_state"):
            state = env["res.country.state"].browse(result["country_state"])
            values["state"] = self._state_id(state.exists())
        if result.get("gender") in GENDERS:
            values["gender"] = result["gender"]
        if result.get("birthdate"):
            values["birthdate"] = self._date_iso(result["birthdate"])
        return self._present(values)
