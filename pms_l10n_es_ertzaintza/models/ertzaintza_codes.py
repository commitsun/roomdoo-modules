# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
"""Plain constants shared by the Ertzaintza A19 client.

This module has no Odoo imports on purpose: the SOAP client, the XML
builder and the unit tests all need these values, and keeping them free of
framework dependencies makes them trivial to import and to test in
isolation.
"""

import re

INSTITUTION_CODE = "ertzaintza"
APPLICATION_NAME = "Roomdoo"

ENDPOINTS = {
    "pre": "https://servicios.pre.ertzaintza.eus/A19/registroHostelero/EnvioFicheroXML",
    "prod": "https://servicios.ertzaintza.eus/A19/registroHostelero/EnvioFicheroXML",
}

WS_NS = "https://ws.negocio.registroHostelero.a19.gvdi.com"
SOAP_ACTION = WS_NS + "/envioFicheroXML"
SOAP_ENV_NS = "http://schemas.xmlsoap.org/soap/envelope/"
PV_NS = "http://www.servicios.ertzaintza.eus/Ertzaintza/ALOJADOS/A19/comunicacionPV"
RH_NS = "http://www.servicios.ertzaintza.eus/Ertzaintza/ALOJADOS/A19/comunicacionRH"

ENTITY_PV = "PV"
ENTITY_RH = "RH"

MAX_COMMUNICATIONS_PER_REQUEST = 400

# pms res.partner.id_category codes -> A19 document type. Anything not
# listed here (e.g. other ID document types) falls back to "OTRO".
DOCUMENT_TYPE_MAP = {"D": "NIF", "N": "NIE", "P": "PAS"}
DOCUMENT_TYPE_DEFAULT = "OTRO"

GENDER_MAP = {"male": "V", "female": "M", "other": "O"}

PAYMENT_TYPES = ("EFECT", "TARJT", "PLATF", "TRANS", "MOVIL", "TREG", "OTRO")

RELATIONSHIP_CODES = (
    "AB",
    "BA",
    "BN",
    "CY",
    "CD",
    "HR",
    "HJ",
    "PA",
    "MA",
    "NI",
    "SB",
    "SG",
    "TI",
    "YN",
    "OT",
)

# SES (pms_l10n_es ses_partners_relationship) code -> A19 code for the
# MINOR. "PM" is gender dependent (PA/MA) -> handled in code, keep here
# only the static part of the map.
RELATIONSHIP_MAP = {c: c for c in RELATIONSHIP_CODES}
RELATIONSHIP_MAP.update({"TU": "OT"})

# Inverse relationship for the ADULT referenced by a minor: minor_code ->
# adult_code (None = depends on the adult's gender: PA/MA).
RELATIONSHIP_INVERSE = {
    "HJ": None,
    "NI": "AB",
    "BN": "BA",
    "SB": "TI",
    "AB": "NI",
    "BA": "BN",
    "PM": "HJ",
    "PA": "HJ",
    "MA": "HJ",
    "TI": "SB",
    "SG": "YN",
    "YN": "SG",
    "HR": "HR",
    "CY": "CY",
    "CD": "CD",
    "TU": "OT",
    "OT": "OT",
}

ERROR_CODES_AUTH = {"403", "PET08", "PET09", "PET10"}
ERROR_CODES_TRANSIENT = {
    "999",
    "900",
    "901",
    "902",
    "903",
    "904",
    "905",
    "906",
    "INT01",
}
ERROR_CODES_DUPLICATE = {"CTO01"}
ERROR_CODES_TOO_MANY = {"PET13", "PET17"}
# Returned when the "solicitud" is empty: it proves the signature and the
# user are accepted, so it is not really an error for our purposes.
ERROR_CODE_EMPTY_REQUEST = "PET07"

# A19 returns codes like "C_1|1_PER43_TI", "C_1_CTO01", "PET07", "403".
# This regex extracts the bare code out of that noise.
ERROR_CODE_REGEX = r"(PET\d{2}|CTO\d{2}|PER\d{2}|COM\d{2}|INT\d{2}|\d{3})"


def extract_error_code(raw_code):
    """Return the bare A19 error code out of a raw response code.

    A19 embeds the code in strings such as "C_1|1_PER43_TI" or
    "C_1_CTO01". When the regex does not match anything (an unexpected
    format), the raw string is returned unchanged so that callers always
    get something to log or compare.
    """
    matches = re.findall(ERROR_CODE_REGEX, raw_code or "")
    if matches:
        return matches[-1]
    return raw_code


def classify_error_codes(codes):
    """Classify a batch of A19 error codes into a single bucket.

    Returns one of "auth", "transient", "duplicate", "too_many" or "data".
    "duplicate" is only returned when EVERY code is CTO01: a mixed batch
    (some duplicates, some real data problems) must still be treated as a
    data problem so that the real problems are not silently swallowed.
    """
    codes = list(codes)
    if any(code in ERROR_CODES_AUTH for code in codes):
        return "auth"
    if any(code in ERROR_CODES_TRANSIENT for code in codes):
        return "transient"
    if codes and all(code in ERROR_CODES_DUPLICATE for code in codes):
        return "duplicate"
    if any(code in ERROR_CODES_TOO_MANY for code in codes):
        return "too_many"
    return "data"
