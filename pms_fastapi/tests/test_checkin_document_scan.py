import datetime
from unittest import mock

from fastapi import status

from odoo.addons.pms.models.pms_property import PmsProperty

from .common import CommonTestCheckin


class TestCheckinDocumentScanEndpoint(CommonTestCheckin):
    def _post(self, payload=None, checkin_partner=None, folio=None):
        url = self._base_url(folio=folio, checkin_partner=checkin_partner or self.guest)
        with self._create_test_client() as test_client:
            self._login(test_client)
            return test_client.post(
                f"{url}/document-scan", json=payload or {"front": "ZnJvbnQ="}
            )

    def _scan(self, result, payload=None):
        self._enable_test_ocr_supplier()
        with mock.patch.object(
            PmsProperty, "_test_document_process", create=True, return_value=result
        ) as process:
            response = self._post(payload)
        return response, process

    def test_scan_maps_the_result(self):
        state = self.env["res.country.state"].search(
            [("country_id", "=", self.country_es.id)], limit=1
        )
        response, process = self._scan(
            {
                "nationality": self.country_pt.id,
                "country_id": self.country_es.id,
                "document_country_id": self.country_pt.id,
                "document_type": self.id_category.id,
                "gender": "female",
                "birthdate": datetime.datetime(1990, 5, 17),
                "firstname": "Ann",
                "lastname": "Lee",
                "lastname2": "Ray",
                "document_number": "AB123",
                "document_support_number": "SUP1",
                "residence_street": "Main 1",
                "residence_city": "Madrid",
                "zip": "28001",
                "country_state": state.id,
            },
            payload={"front": "ZnJvbnQ=", "back": "YmFjaw=="},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        self.assertEqual(
            response.json(),
            {
                "firstname": "Ann",
                "lastname": "Lee",
                "lastname2": "Ray",
                "gender": "female",
                "birthdate": "1990-05-17",
                "nationality": "PT",
                "documentType": "TST",
                "documentNumber": "AB123",
                "documentCountryId": "PT",
                "documentSupportNumber": "SUP1",
                "countryId": "ES",
                "countryState": state.name,
                "residenceCity": "Madrid",
                "residenceStreet": "Main 1",
                "zip": "28001",
            },
        )
        process.assert_called_once_with("ZnJvbnQ=", "YmFjaw==")

    def test_scan_omits_empty_values_and_has_no_back_by_default(self):
        response, process = self._scan(
            {"firstname": "Ann", "lastname": "", "nationality": False, "zip": None}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        self.assertEqual(response.json(), {"firstname": "Ann"})
        process.assert_called_once_with("ZnJvbnQ=", False)

    def test_scan_saves_nothing(self):
        response, _process = self._scan({"firstname": "Ann"})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        self.assertFalse(self.guest.firstname)
        self.assertEqual(self.guest.state, "dummy")

    def test_unreadable_document_is_422(self):
        response, _process = self._scan({})
        self.assertEqual(
            response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY, response.text
        )
        self.assertEqual(response.json()["type"], "/errors/document-not-readable")

    def test_property_without_supplier_is_422(self):
        response = self._post()
        self.assertEqual(
            response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY, response.text
        )
        self.assertEqual(response.json()["type"], "/errors/ocr-not-available")

    def test_partner_of_another_folio_returns_404(self):
        other_folio = self._create_folio()
        self._create_reservation(other_folio, self.room2, adults=1)
        self._enable_test_ocr_supplier()
        with mock.patch.object(
            PmsProperty, "_test_document_process", create=True
        ) as process:
            response = self._post(folio=other_folio)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.text)
        process.assert_not_called()

    def test_front_is_required(self):
        response = self._post({"back": "YmFjaw=="})
        self.assertEqual(
            response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY, response.text
        )

    def test_scan_skips_the_document_lookup_of_the_supplier(self):
        self._enable_test_ocr_supplier()
        seen = []

        def process(property_, front, back):
            seen.append(property_.env.context.get("ocr_skip_document_lookup"))
            return {"firstname": "Ann"}

        with mock.patch.object(
            PmsProperty, "_test_document_process", create=True, new=process
        ):
            response = self._post()
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        self.assertEqual(seen, [True])

    def test_read_only_partner_is_409_and_never_reaches_the_supplier(self):
        self._enable_test_ocr_supplier()
        for state in ("done", "cancel"):
            self.guest.state = state
            with mock.patch.object(
                PmsProperty, "_test_document_process", create=True
            ) as process:
                response = self._post()
            self.assertEqual(
                response.status_code, status.HTTP_409_CONFLICT, response.text
            )
            self.assertEqual(
                response.json()["type"], "/errors/checkin-partner-read-only"
            )
            process.assert_not_called()
