# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
"""Build the ``alt:peticion`` document the Ertzaintza A19 service expects.

Plain functions on purpose (no AbstractModel): they take Odoo recordsets but
do not write anything, so they can be imported and unit tested directly
without going through the communication model.

The data is the one of the RD 933/2021, the same Roomdoo already collects for
SES, so the mapping mirrors ``pms_l10n_es/wizards/traveller_report.py``. The
differences with SES are deliberate and documented inline: the root
namespace, the extra ``nacionalidad``/``sexo`` elements, the contract date
rule of the traveller report, the payment codes and, above all, that names
keep their accents here.
"""
import csv
import functools
import logging
import re
from datetime import datetime

from lxml import etree
from lxml.etree import QName

from odoo import _
from odoo.modules.module import get_module_resource

from .ertzaintza_codes import (
    DOCUMENT_TYPE_DEFAULT,
    DOCUMENT_TYPE_MAP,
    ENTITY_PV,
    ENTITY_RH,
    GENDER_MAP,
    MAX_COMMUNICATIONS_PER_REQUEST,
    PV_NS,
    RELATIONSHIP_INVERSE,
    RELATIONSHIP_MAP,
    RH_NS,
)

_logger = logging.getLogger(__name__)

ADULT_AGE = 18
SPAIN_CODE = "ES"
SPAIN_ALPHA3 = "ESP"
HOUR_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
EMAIL_RE = re.compile(r"^[^@]+@[^.]+\..+$")
DOCUMENT_CLEAN_RE = re.compile(r"[^A-Za-z0-9]")
PHONE_CLEAN_RE = re.compile(r"[^+0-9]")
WITH_SUPPORT_NUMBER = ("NIF", "NIE")


def _clean(value, length):
    """Collapse whitespace and truncate, keeping accents and ``ñ``.

    SES strips every non-ASCII letter (``clean_string_only_letters``); the A19
    schemas accept any character, so doing the same here would report guests
    under a mangled name.
    """
    if not value:
        return ""
    return re.sub(r"\s{2,}", " ", str(value).strip())[:length]


def _text(parent, tag, value):
    """Append ``<tag>value</tag>`` to ``parent`` when there is a value."""
    if value in (None, "", False):
        return None
    element = etree.SubElement(parent, tag)
    element.text = str(value)
    return element


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    return value


def _age_at(birthdate, on_date):
    """Age in years at ``on_date``; ``None`` when the birth date is unknown."""
    if not birthdate or not on_date:
        return None
    birthdate = _as_date(birthdate)
    on_date = _as_date(on_date)
    return (
        on_date.year
        - birthdate.year
        - ((on_date.month, on_date.day) < (birthdate.month, birthdate.day))
    )


def _datetime_text(day, hour):
    """``YYYY-MM-DDThh:mm:ss`` from a date and an ``HH:MM`` string."""
    hour = hour if hour and HOUR_RE.match(hour or "") else "00:00"
    return f"{_as_date(day).isoformat()}T{hour}:00"


@functools.lru_cache(maxsize=1)
def _municipality_by_zip():
    """Map Spanish postal code -> 5 digit INE municipality code.

    Read once per process: the SES helper re-reads the whole 14 000 row file
    for every guest.
    """
    path = get_module_resource(
        "pms_l10n_es", "static/src", "pms.ine.zip.municipality.ine.relation.csv"
    )
    mapping = {}
    if not path:  # pragma: no cover - the SES module is a dependency
        _logger.warning("The INE zip/municipality relation file was not found.")
        return mapping
    with open(path, newline="") as csv_file:
        for row in csv.reader(csv_file):
            if len(row) < 2 or not row[0].strip().isdigit():
                continue  # header
            mapping[row[0].strip().zfill(5)] = row[1].strip()[:5]
    return mapping


def municipality_code(zip_code):
    """INE municipality code of a Spanish postal code, or ``""``.

    Unlike SES, there is no fallback to the establishment's own postal code:
    reporting the hotel's municipality as the guest's residence would be
    wrong data, and the A19 service accepts ``nombreMunicipio`` instead.
    """
    if not zip_code:
        return ""
    return _municipality_by_zip().get(str(zip_code).strip().zfill(5), "")


def _phone(value):
    if not value:
        return ""
    return PHONE_CLEAN_RE.sub("", str(value))[:20]


def _email(value):
    if not value:
        return ""
    value = str(value).strip()[:50]
    return value if EMAIL_RE.match(value) else ""


def _document_type(category_code):
    return DOCUMENT_TYPE_MAP.get(category_code, DOCUMENT_TYPE_DEFAULT)


def _document_number(value):
    if not value:
        return ""
    return DOCUMENT_CLEAN_RE.sub("", str(value)).upper()[:15]


def _person_label(record, fallback):
    name = " ".join(
        part
        for part in (
            getattr(record, "firstname", "") or "",
            getattr(record, "lastname", "") or "",
        )
        if part
    ).strip()
    return name or fallback


# --------------------------------------------------------------------------
# Contact resolution
# --------------------------------------------------------------------------
def _contact_candidates(reservation, checkin_partner=None):
    """Phones and emails to try, in order of preference."""
    candidates = []
    if checkin_partner:
        candidates += [
            checkin_partner.mobile,
            checkin_partner.phone,
            checkin_partner.email,
        ]
    partner = reservation.partner_id
    candidates += [
        partner.mobile,
        partner.phone,
        partner.email,
        reservation.mobile,
        reservation.email,
        reservation.pms_property_id.partner_id.mobile,
        reservation.pms_property_id.partner_id.phone,
        reservation.pms_property_id.partner_id.email,
    ]
    return [value for value in candidates if value]


def _add_contact_elements(persona, reservation, checkin_partner=None):
    """Add ``telefono``/``telefono2``/``correo``; True when at least one."""
    phones, email = [], ""
    for candidate in _contact_candidates(reservation, checkin_partner):
        if "@" in str(candidate):
            email = email or _email(candidate)
        else:
            phone = _phone(candidate)
            if phone and phone not in phones:
                phones.append(phone)
    for tag, value in zip(("telefono", "telefono2"), phones, strict=False):
        _text(persona, tag, value)
    _text(persona, "correo", email)
    return bool(phones or email)


# --------------------------------------------------------------------------
# Address
# --------------------------------------------------------------------------
def _add_address_elements(persona, record, label, problems):
    """Add the ``direccion`` block of a guest or partner."""
    direccion = etree.SubElement(persona, "direccion")
    street = _clean(record.street, 100)
    if not street:
        problems.append(_("%s: the address is missing.", label))
    _text(direccion, "direccion", street)
    _text(direccion, "direccionComplementaria", _clean(record.street2, 100))
    country = record.country_id
    if not country and record._name == "pms.checkin.partner":
        # ``_compute_country_id`` of pms clears the country of residence when
        # the guest carries no state, so fall back to the contact behind it.
        country = record.partner_id.country_id
    code = municipality_code(record.zip) if country.code == SPAIN_CODE else ""
    city = _clean(record.city, 100)
    _text(direccion, "codigoMunicipio", code)
    _text(direccion, "nombreMunicipio", city)
    if not code and not city:
        problems.append(
            _("%s: neither the municipality code nor its name are known.", label)
        )
    if not record.zip:
        problems.append(_("%s: the postal code is missing.", label))
    _text(direccion, "codigoPostal", _clean(record.zip, 20))
    if not country.code_alpha3:
        problems.append(_("%s: the country of residence is missing.", label))
    _text(direccion, "pais", country.code_alpha3)


# --------------------------------------------------------------------------
# Contract
# --------------------------------------------------------------------------
def _payment_elements(contrato, reservation):
    """``pago`` block. A19 has no ``DESTI`` code (SES does), so unpaid
    reservations are reported as ``OTRO`` to avoid the CTO05 rejection."""
    pago = etree.SubElement(contrato, "pago")
    payments = reservation.folio_id.payment_ids.filtered(
        lambda payment: payment.state == "posted"
    )
    payment_date = False
    if not payments:
        payment_type = "OTRO"
    else:
        payment = payments[0]
        payment_date = payment.date
        cash = payments.filtered(lambda p: p.journal_id.type == "cash")
        if cash:
            payment_type = "EFECT"
            payment_date = cash[0].date
        elif reservation.sale_channel_origin_id.channel_type == "indirect":
            payment_type = "PLATF"
        else:
            payment_type = "TARJT"
    _text(pago, "tipoPago", payment_type)
    _text(pago, "fechaPago", payment_date and _as_date(payment_date).isoformat())


def _add_contract_elements(comunicacion, reservation, reference, entity, people):
    contrato = etree.SubElement(comunicacion, "contrato")
    _text(contrato, "referencia", _clean(reference, 50))
    if entity == ENTITY_PV:
        # The A19 service requires the entry date to be the contract date or
        # the day after (CTO17): the contract date of a traveller report is
        # the day the guest signs the check-in sheet, never the booking date.
        contract_date = _as_date(reservation.checkin)
    else:
        booked = _as_date(reservation.date_order) or _as_date(reservation.checkin)
        contract_date = min(booked, _as_date(reservation.checkin))
    _text(contrato, "fechaContrato", contract_date.isoformat())
    _text(
        contrato,
        "fechaEntrada",
        _datetime_text(reservation.checkin, reservation.arrival_hour),
    )
    _text(
        contrato,
        "fechaSalida",
        _datetime_text(reservation.checkout, reservation.departure_hour),
    )
    _text(contrato, "numPersonas", people)
    _text(contrato, "numHabitaciones", 1)
    _payment_elements(contrato, reservation)


# --------------------------------------------------------------------------
# People
# --------------------------------------------------------------------------
def _pick_holder(reservation, guests):
    """The single ``TI`` of the communication (A19 errors COM07/COM08)."""
    if not guests:
        return None
    partner = reservation.partner_id
    if partner:
        same = guests.filtered(lambda guest: guest.partner_id == partner)
        if same:
            return same[0]
    adults = guests.filtered(
        lambda guest: (_age_at(guest.birthdate_date, reservation.checkin) or 0)
        >= ADULT_AGE
    )
    with_document = adults.filtered(lambda guest: guest.document_number)
    return (with_document or adults or guests)[0]


def _relationship_codes(guests, reservation):
    """Map guest id -> ``parentesco`` code.

    The minor carries the role of the adult it is related to (a child of a
    father reports ``PA``), and that adult carries the inverse (``HJ``), so
    both ends of the relationship are self consistent. Pre-production accepts
    communications without any ``parentesco``, so a missing relationship is
    never reported as a problem.
    """
    codes = {}
    for guest in guests:
        age = _age_at(guest.birthdate_date, reservation.checkin)
        if age is None or age >= ADULT_AGE:
            continue
        raw = guest.ses_partners_relationship
        if not raw:
            continue
        adult = guest.ses_related_checkin_partner_id
        code = RELATIONSHIP_MAP.get(raw, "OT")
        if raw == "PM":
            code = "MA" if adult.gender == "female" else "PA"
        codes[guest.id] = code
        if adult and adult in guests:
            inverse = RELATIONSHIP_INVERSE.get(raw, "OT")
            if inverse is None:
                inverse = "MA" if adult.gender == "female" else "PA"
            codes.setdefault(adult.id, inverse)
    return codes


def _add_guest_person(comunicacion, guest, reservation, role, relationship, problems):
    """One ``persona`` of a traveller report (``personaHospedajeType``)."""
    label = _person_label(guest, _("Guest of %s", reservation.name))
    persona = etree.SubElement(comunicacion, "persona")
    _text(persona, "rol", role)

    name = _clean(guest.firstname, 50)
    lastname = _clean(guest.lastname, 50)
    if not name:
        problems.append(_("%s: the first name is missing.", label))
    if not lastname:
        problems.append(_("%s: the surname is missing.", label))
    _text(persona, "nombre", name)
    _text(persona, "apellido1", lastname)

    nationality = (
        guest.nationality_id.code_alpha3 or guest.document_country_id.code_alpha3 or ""
    )
    lastname2 = _clean(guest.lastname2, 50)
    if nationality == SPAIN_ALPHA3 and not lastname2:
        problems.append(
            _("%s: the second surname is mandatory for Spanish guests.", label)
        )
    _text(persona, "apellido2", lastname2)

    age = _age_at(guest.birthdate_date, reservation.checkin)
    document_type = _document_type(guest.document_type.code)
    document_number = _document_number(guest.document_number)
    if age is not None and age >= ADULT_AGE and not document_number:
        problems.append(_("%s: the identity document is mandatory for adults.", label))
    if document_number:
        _text(persona, "tipoDocumento", document_type)
        _text(persona, "numeroDocumento", document_number)
        support = _clean(guest.support_number, 9)
        if document_type in WITH_SUPPORT_NUMBER and not support:
            problems.append(
                _("%s: the support number of the %s is missing.", label, document_type)
            )
        _text(persona, "soporteDocumento", support)

    if not guest.birthdate_date:
        problems.append(_("%s: the birth date is missing.", label))
    _text(
        persona,
        "fechaNacimiento",
        guest.birthdate_date and _as_date(guest.birthdate_date).isoformat(),
    )
    if not nationality:
        problems.append(_("%s: the nationality is missing.", label))
    _text(persona, "nacionalidad", nationality)
    _text(persona, "sexo", GENDER_MAP.get(guest.gender))

    _add_address_elements(persona, guest, label, problems)
    if not _add_contact_elements(persona, reservation, guest):
        problems.append(_("%s: neither a phone number nor an email are known.", label))
    _text(persona, "parentesco", relationship)


def _add_holder_person(comunicacion, reservation, problems):
    """The single ``persona`` of a reservation (``personaReservaType``)."""
    label = _("Reservation %s", reservation.name)
    partner = reservation.partner_id
    persona = etree.SubElement(comunicacion, "persona")
    _text(persona, "rol", "TI")

    name = _clean(partner.firstname, 50)
    lastname = _clean(partner.lastname, 50)
    if not name and reservation.partner_name:
        # Same fallback as SES: split the free text name of the reservation.
        parts = _clean(reservation.partner_name, 120).split(" ")
        name = parts[0][:50]
        lastname = parts[1][:50] if len(parts) > 1 else "No aplica"
    if not name:
        problems.append(_("%s: the holder name is missing.", label))
    _text(persona, "nombre", name)
    _text(persona, "apellido1", lastname)
    _text(persona, "apellido2", _clean(partner.lastname2, 50))

    document = partner.id_numbers.filtered(
        lambda number: number.category_id.code in DOCUMENT_TYPE_MAP
    )[:1]
    if document:
        _text(persona, "tipoDocumento", _document_type(document.category_id.code))
        _text(persona, "numeroDocumento", _document_number(document.name))
    _text(
        persona,
        "fechaNacimiento",
        partner.birthdate_date and _as_date(partner.birthdate_date).isoformat(),
    )
    _text(persona, "nacionalidad", partner.nationality_id.code_alpha3)
    _text(persona, "sexo", GENDER_MAP.get(partner.gender))
    if partner.street and partner.zip and partner.country_id.code_alpha3:
        _add_address_elements(persona, partner, label, problems)
    if not _add_contact_elements(persona, reservation):
        problems.append(_("%s: neither a phone number nor an email are known.", label))


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
def build_solicitud(entity, items):
    """Return ``(xml_string, problems)`` for a batch of communications.

    :param entity: ``"PV"`` (traveller report) or ``"RH"`` (reservation).
    :param items: list of dicts, one per ``comunicacion`` block, with keys
        ``reservation`` (``pms.reservation`` record, all of the same
        property), ``reference`` (``contrato/referencia``) and, for PV,
        ``checkin_partners`` (the guests to report).
    :returns: ``(xml_string, problems)``. When ``problems`` is not empty the
        XML is ``""`` and nothing must be sent.
    """
    problems = []
    if not items:
        return "", [_("There is nothing to report.")]
    if len(items) > MAX_COMMUNICATIONS_PER_REQUEST:
        return "", [
            _(
                "The Ertzaintza accepts at most %s communications per request.",
                MAX_COMMUNICATIONS_PER_REQUEST,
            )
        ]
    properties = {item["reservation"].pms_property_id for item in items}
    if len(properties) > 1:
        return "", [_("Every communication must belong to the same property.")]

    pms_property = items[0]["reservation"].pms_property_id
    establishment = _clean(pms_property.institution_property_id, 10)
    if not establishment:
        problems.append(
            _(
                "The property %s has no establishment code for the Ertzaintza.",
                pms_property.name,
            )
        )

    namespace = PV_NS if entity == ENTITY_PV else RH_NS
    root = etree.Element(QName(namespace, "peticion"), nsmap={"alt": namespace})
    solicitud = etree.SubElement(root, "solicitud")
    if entity == ENTITY_PV:
        _text(solicitud, "codigoEstablecimiento", establishment)

    for item in items:
        reservation = item["reservation"]
        reference = item.get("reference") or reservation.name
        comunicacion = etree.SubElement(solicitud, "comunicacion")
        if entity == ENTITY_RH:
            establecimiento = etree.SubElement(comunicacion, "establecimiento")
            _text(establecimiento, "codigo", establishment)
            _add_contract_elements(
                comunicacion,
                reservation,
                reference,
                entity,
                (reservation.adults or 0) + (reservation.children or 0),
            )
            _add_holder_person(comunicacion, reservation, problems)
            continue

        guests = item.get("checkin_partners")
        if not guests:
            problems.append(
                _("Reservation %s: there is no guest to report.", reservation.name)
            )
            continue
        _add_contract_elements(
            comunicacion, reservation, reference, entity, len(guests)
        )
        holder = _pick_holder(reservation, guests)
        relationships = _relationship_codes(guests, reservation)
        for guest in guests:
            _add_guest_person(
                comunicacion,
                guest,
                reservation,
                "TI" if guest == holder else "VI",
                relationships.get(guest.id),
                problems,
            )

    if problems:
        return "", problems
    return etree.tostring(root, encoding="unicode"), []


@functools.lru_cache(maxsize=2)
def _schema(entity):
    path = get_module_resource(
        "pms_l10n_es_ertzaintza", "static/xsd", "comunicacion%s.xsd" % entity
    )
    return etree.XMLSchema(etree.parse(path))


def validate_xsd(xml_string, entity):
    """Validate ``xml_string`` against the bundled XSD of ``entity``.

    :returns: list of error strings; empty when the document is valid.
    """
    if entity not in (ENTITY_PV, ENTITY_RH):
        return [_("Unknown communication type %s.", entity)]
    try:
        document = etree.fromstring((xml_string or "").encode("utf-8"))
    except etree.XMLSyntaxError as error:
        return [_("The generated document is not valid XML: %s", error)]
    schema = _schema(entity)
    if schema.validate(document):
        return []
    return [str(error) for error in schema.error_log]
