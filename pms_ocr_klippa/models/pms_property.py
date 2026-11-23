import logging
import re
import traceback
import unicodedata
from datetime import date, datetime

import requests
from dateutil.relativedelta import relativedelta
from requests.adapters import HTTPAdapter
from thefuzz import process
from urllib3.util.retry import Retry

from odoo import _, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

# Official Spanish street directory (IGN)
CARTOCIUDAD_URL = "https://www.cartociudad.es/geocoder/api/geocoder/candidates"
CARTOCIUDAD_TIMEOUT = 3
# Articles and joining words, which do not tell places apart
PLACE_STOPWORDS = set(
    "a as d de del dels el els es i l la las les lo los o os sa ses y".split()
)
# A town read ending with "de", "la"... was cut by the document, and so was one
# starting with "de", of which only the end was read
CUT_PLACE_NAME = re.compile(
    r"^\W*d(e|el|els)?\b|\b(de|del|dels|d|la|las|les|los|el|l)\W*$"
)

CHECKIN_FIELDS = {
    "nationality": "partner_id.nationality_id.id",
    "country_id": "partner_id.residence_partner_id.country_id.id",
    "firstname": "partner_id.firstname",
    "lastname": "partner_id.lastname",
    "lastname2": "partner_id.lastname2",
    "gender": "partner_id.gender",
    "birthdate": "partner_id.birthdate_date",
    "document_type": "category_id.id",
    "document_expedition_date": "valid_from",
    "document_support_number": "support_number",
    "document_number": "name",
    "residence_street": "partner_id.residence_partner_id.street",
    "residence_city": "partner_id.residence_partner_id.city",
    "country_state": "partner_id.residence_partner_id.state_id.id",
    "document_country_id": "country_id.id",
    "zip": "partner_id.residence_partner_id.zip",
}


def _place_names(name):
    """Each name of a place ("Alacant/Alicante", "Bizkaia (Vizcaya)") as the
    set of its words, without accents, case, articles or joining words."""
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    names = (
        set(re.findall(r"[a-z0-9]+", part)) - PLACE_STOPWORDS
        for part in re.split(r"[/()]", text.lower())
    )
    return [words for words in names if words]


def _same_place(name, other):
    """Whether the first place name names the other place: by one of its
    names or, when the first was cut ("El Prat de"), by part of one."""
    names, other_names = _place_names(name), _place_names(other)
    if any(words in other_names for words in names):
        return True
    return bool(CUT_PLACE_NAME.search((name or "").lower())) and any(
        words < other_words for words in names for other_words in other_names
    )


class PmsProperty(models.Model):
    _inherit = "pms.property"

    ocr_checkin_supplier = fields.Selection(selection_add=[("klippa", "Klippa")])
    save_images_log = fields.Boolean(
        string="Save Images Logs",
        help="Indicates if the images sent to the OCR service are saved in the logs",
        default=False,
    )

    # flake8: noqa: C901
    def _klippa_document_process(self, image_base_64_front, image_base_64_back=False):
        try:
            self.ensure_one()
            ocr_klippa_url = (
                self.env["ir.config_parameter"].sudo().get_param("ocr_klippa_url")
            )
            ocr_klippa_api_key = (
                self.env["ir.config_parameter"].sudo().get_param("ocr_klippa_api_key")
            )
            document = []
            if image_base_64_front:
                document.append(image_base_64_front)
            if image_base_64_back:
                document.append(image_base_64_back)
            if not document:
                raise ValidationError(_("No document image found"))

            headers = {
                "X-Auth-Key": ocr_klippa_api_key,
                "Content-Type": "application/json",
            }
            payload = {
                "document": document,
            }
            request_size = (len(image_base_64_front) if image_base_64_front else 0) + (
                len(image_base_64_back) if image_base_64_back else 0
            )
            log_data = {
                "pms_property_id": self.id,
                "request_datetime": datetime.now(),
                "endpoint": ocr_klippa_url,
                "request_size": request_size,
                "request_headers": str(headers),
            }
            # TODO: Improvement this to save log only in error case
            if self.save_images_log:
                log_data.update(
                    {
                        "image_base64_front": image_base_64_front,
                        "image_base64_back": image_base_64_back,
                    }
                )

            # Call Klippa OCR API
            result = requests.post(
                ocr_klippa_url,
                headers=headers,
                json=payload,
                timeout=20,
            )
            json_data = result.json()
            log_data.update(
                {
                    "klippa_status": json_data.get("result", "error"),
                    "response_datetime": datetime.now(),
                    "response_size": len(str(json_data)),
                    "request_duration": (
                        datetime.now() - log_data["request_datetime"]
                    ).seconds,
                    "request_id": json_data.get("request_id", False),
                }
            )
            if self.save_images_log:
                log_data.update(
                    {
                        "klippa_response": json_data,
                    }
                )

            if json_data.get("result") != "success":
                raise ValidationError(_("Error calling Klippa OCR API"))
            document_data = json_data["data"]["parsed"]
            init_mapped_datetime = datetime.now()

            mapped_data = self._map_klippa_data(document_data)

            if mapped_data.get("nominatim_status"):
                log_data.update(
                    {
                        "nominatim_status": mapped_data["nominatim_status"],
                    }
                )
                mapped_data.pop("nominatim_status")
            if mapped_data.get("nominatim_response"):
                log_data.update(
                    {
                        "nominatim_response": mapped_data["nominatim_response"],
                    }
                )
                mapped_data.pop("nominatim_response")

            log_data.update(
                {
                    "service_response": mapped_data,
                    "mapped_duration": (datetime.now() - init_mapped_datetime).seconds,
                    "total_duration": (
                        datetime.now() - log_data["request_datetime"]
                    ).seconds,
                    "final_status": "success",
                }
            )
            self.env["klippa.log"].sudo().create(log_data)
            return mapped_data
        except Exception:
            log_data.update(
                {
                    "error": traceback.format_exc(),
                    "final_status": "error",
                    "total_duration": (
                        datetime.now() - log_data["request_datetime"]
                    ).seconds,
                }
            )
            self.env["klippa.log"].sudo().create(log_data)
            _logger.error(traceback.format_exc())
            return {}

    def _map_klippa_data(self, document_data):
        mapped_data = {}
        key_document_number, key_personal_number = self._get_number_keys(document_data)
        for key, dict_value in document_data.items():
            if dict_value and isinstance(dict_value, dict):
                value = dict_value.get("value", False)
            else:
                continue
            # Residence Address --------------------------------------------------
            if key == "address" and value:
                mapped_data = self._complete_residence_address(
                    value,
                    mapped_data,
                    document_country_id=self._get_country_id(
                        (document_data.get("issuing_country") or {}).get("value")
                    ),
                )

            # Document Data --------------------------------------------------
            elif key == "issuing_country" and value:
                mapped_data["document_country_id"] = self._get_country_id(value)
            elif key == "document_type" and value:
                mapped_data["document_type"] = self._get_document_type(
                    klippa_type=value,
                    klippa_subtype=document_data.get("document_subtype").get("value")
                    if document_data.get("document_subtype")
                    else False,
                    country_id=self._get_country_id(
                        document_data.get("issuing_country").get("value")
                        if document_data.get("issuing_country")
                        else False
                    ),
                ).id
            elif key == "personal_number" and value:
                mapped_data[key_personal_number] = value
            elif key == "document_number" and value:
                mapped_data[key_document_number] = value
            elif key == "date_of_issue" and value:
                mapped_data["document_expedition_date"] = datetime.strptime(
                    value, "%Y-%m-%dT%H:%M:%S"
                ).date()
            elif (
                key == "date_of_expiry"
                and value
                and not document_data.get("date_of_issue", False)
            ):
                mapped_data["document_expedition_date"] = self._calc_expedition_date(
                    document_class_code=self._get_document_type(
                        klippa_type=document_data.get("document_class_code", False),
                        klippa_subtype=document_data.get("document_subtype").get(
                            "value"
                        )
                        if document_data.get("document_subtype")
                        else False,
                        country_id=self._get_country_id(
                            document_data.get("issuing_country").get("value")
                            if document_data.get("issuing_country")
                            else False
                        ),
                    ),
                    date_of_expiry=value,
                    age=False,
                    date_of_birth=document_data.get("date_of_birth", False),
                )

            # Personal Data --------------------------------------------------
            elif key == "gender" and value:
                if value == "M":
                    mapped_data["gender"] = "male"
                elif value == "F":
                    mapped_data["gender"] = "female"
                else:
                    mapped_data["gender"] = "other"
            elif key == "given_names" and value:
                mapped_data["firstname"] = value
            elif key == "surname" and value:
                mapped_data["lastname"] = self._get_surnames(
                    origin_surname=value,
                )[0]
                mapped_data["lastname2"] = self._get_surnames(
                    origin_surname=value,
                )[1]
            elif key == "date_of_birth" and value:
                mapped_data["birthdate"] = datetime.strptime(
                    value, "%Y-%m-%dT%H:%M:%S"
                ).date()
            elif key == "nationality" and value:
                mapped_data["nationality"] = self._get_country_id(value)

        # If the document number exist and not get the complete checkin information
        # recovery the lost data from the found document
        if mapped_data.get("document_number") and not all(
            [mapped_data.get(field, False) for field in CHECKIN_FIELDS]
        ):
            document = self.env["res.partner.id_number"].search(
                [
                    ("name", "=", mapped_data["document_number"]),
                ],
                limit=1,
            )
            if document:
                mapped_data = self._complete_mapped_from_partner(document, mapped_data)

        return mapped_data

    def _calc_expedition_date(
        self, document_class_code, date_of_expiry, age, date_of_birth
    ):
        result = False
        person_age = False
        if age:
            person_age = age
        elif date_of_birth and date_of_birth.get("value") != "":
            date_of_birth = datetime.strptime(
                date_of_birth.get("value"), "%Y-%m-%dT%H:%M:%S"
            ).date()
            person_age = relativedelta(date.today(), date_of_birth).years
        if date_of_expiry and date_of_expiry != "" and person_age:
            date_of_expiry = datetime.strptime(
                date_of_expiry, "%Y-%m-%dT%H:%M:%S"
            ).date()
            if person_age < 30:
                result = date_of_expiry - relativedelta(years=5)
            elif (
                person_age >= 30
                and document_class_code
                and document_class_code.code == "P"
            ):
                result = date_of_expiry - relativedelta(years=10)
            elif 30 <= person_age < 70:
                result = date_of_expiry - relativedelta(years=10)
        return result if result else False

    def _get_number_keys(self, document_data):
        # Heuristic to identify the mapping of document_number and document_support_number
        # with respect to the personal_number and document_number fields of klippa
        # If the klippa document type is "I", and it is Spanish, then the personal_number
        # we map it against document_number and document_number against document_support_number
        # otherwise, the document_number we map against document_number and the personal_number
        # against document_support_number
        key_document_number = "document_number"
        key_personal_number = "document_support_number"
        if (
            document_data.get("document_type", False)
            and document_data.get("document_type").get("value") == "I"
            and document_data.get("issuing_country", False)
            and document_data.get("issuing_country").get("value") == "ESP"
        ):
            key_document_number = "document_support_number"
            key_personal_number = "document_number"
        return (key_document_number, key_personal_number)

    def _get_document_type(self, klippa_type, klippa_subtype=False, country_id=False):
        # If we hace the issuing country, and document type is configured in the system
        # to be used with the country, we use the country to get the document type
        # If have issuing country and not found document type, we search a document type
        # without country
        # If not have issuing country, we search the document only by klippa code
        document_type = False
        domain = [("klippa_code", "=", klippa_type)]
        if country_id:
            domain.append(("country_ids", "in", country_id))
        document_type = self.env["res.partner.id_category"].search(domain)
        if not document_type and country_id:
            document_type = self.env["res.partner.id_category"].search(
                [
                    ("klippa_code", "=", klippa_type),
                    ("country_ids", "=", False),
                ],
            )
        elif not document_type:
            document_type = self.env["res.partner.id_category"].search(
                [
                    ("klippa_code", "=", klippa_type),
                ],
            )
        if len(document_type) > 1 and klippa_subtype:
            # Try find document type by klippa_subtype_code, if not found, get the first
            document_subtype = document_type.filtered(
                lambda dt: dt.klippa_subtype_code == klippa_subtype
            )
            document_type = document_subtype[0] if document_subtype else False
        if not document_type:
            document_type = self.env.ref(
                "pms_partner_identification.document_type_other"
            )
        return document_type[0] if document_type else False

    def _get_country_id(self, country_code):
        if not country_code:
            return False
        return (
            self.env["res.country"]
            .search([("code_alpha3", "=", country_code)], limit=1)
            .id
        )

    def _get_state(self, name, country_id):
        """The state with that name, in the country when we know it: by its
        words first, as close names can score the same ("VALENCIA" against
        València and León), then the best fuzzy match if it is the only one.
        None when several states fit ("Córdoba")."""
        if not name:
            return self.env["res.country.state"]
        states = self.env["res.country.state"].search(
            [("country_id", "=", country_id)] if country_id else []
        )
        found = states.filtered(lambda state: _same_place(name, state.name))
        if not found:
            matches = process.extract(name, {s.id: s.name for s in states}, limit=2)
            if (
                matches
                and matches[0][1] >= 90
                and (len(matches) == 1 or matches[0][1] > matches[1][1])
            ):
                found = states.browse(matches[0][2])
        return found if len(found) == 1 else states.browse()

    def _get_surnames(self, origin_surname):
        # If origin surname has two or more surnames
        # Get the last word like lastname2 and the rest like lastname
        surnames = origin_surname.split(" ")
        if len(surnames) > 1:
            return (" ".join(surnames[:-1]), surnames[-1])
        return (origin_surname, False)

    def _complete_residence_address(
        self, value, mapped_data, document_country_id=False
    ):
        """Map the residence address read from the document. Spanish ID cards
        have no zip, so it is completed with CartoCiudad."""
        if "street_name" in value:
            mapped_data["residence_street"] = value["street_name"] + (
                " " + value["house_number"] if "house_number" in value else ""
            )
        if "city" in value:
            mapped_data["residence_city"] = value["city"]
        # Without a country, the address of an ID card is one of the country
        # that issues it
        country_id = self._get_country_id(value.get("country")) or document_country_id
        if country_id:
            mapped_data["country_id"] = country_id
        if "province" in value:
            country_state = self._get_state(value["province"], country_id)
            mapped_data["country_state"] = country_state.id
            if country_state and not country_id:
                mapped_data["country_id"] = country_state.country_id.id
        in_spain = mapped_data.get("country_id") == self.env.ref("base.es").id
        postcode = value.get("postcode")
        # A Spanish zip has five digits: "Po1" is the floor read as a zip
        if postcode and (not in_spain or re.fullmatch(r"\d{5}", postcode)):
            mapped_data["zip"] = postcode
            domain = [("name", "=", postcode)]
            if mapped_data.get("country_id"):
                domain.append(("city_id.country_id", "=", mapped_data["country_id"]))
            zip_code = self.env["res.city.zip"].search(domain, limit=1)
            if zip_code:
                mapped_data["residence_city"] = (
                    zip_code.city_id.name
                    if not mapped_data.get("residence_city", False)
                    else mapped_data["residence_city"]
                )
                mapped_data["country_state"] = (
                    zip_code.city_id.state_id.id
                    if not mapped_data.get("country_state", False)
                    else mapped_data["country_state"]
                )
                mapped_data["country_id"] = (
                    zip_code.city_id.state_id.country_id.id
                    if not mapped_data.get("country_id", False)
                    else mapped_data["country_id"]
                )
        elif in_spain:
            mapped_data = self._complete_spanish_zip(value, mapped_data)
        return mapped_data

    def _complete_spanish_zip(self, value, mapped_data):
        """Take the zip CartoCiudad gives for the street number read, only
        from a portal in the town and state of the document."""
        number = re.match(r"\d+", value.get("house_number") or "")
        if not (value.get("street_name") and value.get("city") and number):
            return mapped_data
        # int() drops the zeros documents may print ("0007")
        streets = [f"{value['street_name']} {int(number.group())}"]
        # The directory may not know a leading abbreviation ("Crer.", "Lgar.")
        bare_street = re.sub(r"^\w{1,5}\.\s*", "", streets[0])
        if bare_street != streets[0]:
            streets.append(bare_street)
        try:
            for street in streets:
                candidates = self._get_cartociudad_candidates(
                    f"{street}, {value['city']}"
                )
                if candidates:
                    break
        except (requests.RequestException, ValueError):
            _logger.warning("Zip lookup in CartoCiudad failed", exc_info=True)
            mapped_data["nominatim_status"] = "error"
            mapped_data["nominatim_response"] = traceback.format_exc()
            return mapped_data
        mapped_data["nominatim_status"] = "not_found"
        if candidates:
            mapped_data["nominatim_response"] = candidates
        state = self.env["res.country.state"].browse(mapped_data.get("country_state"))
        for candidate in candidates:
            if candidate.get("type") != "portal" or not candidate.get("postalCode"):
                continue
            if not (
                _same_place(value["city"], candidate.get("muni"))
                or _same_place(value["city"], candidate.get("poblacion"))
            ):
                continue
            candidate_state = self._get_state(
                candidate.get("province"), mapped_data["country_id"]
            )
            if state and candidate_state != state:
                continue
            mapped_data["zip"] = candidate["postalCode"]
            mapped_data["country_state"] = candidate_state.id
            mapped_data["nominatim_status"] = "success"
            break
        return mapped_data

    def _get_cartociudad_candidates(self, query):
        with requests.Session() as session:
            # One retry on connection or server errors, never waiting for the
            # Retry-After of an answer: the guest is waiting for the scan
            retry = Retry(
                total=1,
                status_forcelist=(500, 502, 503, 504),
                respect_retry_after_header=False,
            )
            session.mount("https://", HTTPAdapter(max_retries=retry))
            response = session.get(
                CARTOCIUDAD_URL,
                params={"q": query, "limit": 5},
                timeout=CARTOCIUDAD_TIMEOUT,
            )
            response.raise_for_status()
            return response.json() if response.content else []

    def _complete_mapped_from_partner(self, document, mapped_data):
        for key, field in CHECKIN_FIELDS.items():
            if (
                not mapped_data.get(key, False)
                and document.mapped(field)
                and document.mapped(field)[0]
            ):
                mapped_data[key] = document.mapped(field)[0]
        return mapped_data
