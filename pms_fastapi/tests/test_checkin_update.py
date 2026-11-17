import base64
import io
import json
from unittest import mock

from fastapi import status
from PIL import Image

from odoo.addons.pms.models.pms_checkin_partner import PmsCheckinPartner

from .common import CommonTestCheckin


def _png_base64():
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "black").save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


class TestCheckinUpdateEndpoint(CommonTestCheckin):
    def _patch(self, payload, checkin_partner=None, folio=None, content_type=None):
        url = self._base_url(folio=folio, checkin_partner=checkin_partner or self.guest)
        with self._create_test_client() as test_client:
            self._login(test_client)
            if content_type:
                return test_client.patch(
                    url,
                    content=json.dumps(payload),
                    headers={"Content-Type": content_type},
                )
            return test_client.patch(url, json=payload)

    def _assert_problem(self, response, type_, field=None):
        self.assertEqual(
            response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY, response.text
        )
        self.assertEqual(response.json()["type"], type_)
        if field:
            self.assertEqual(response.json()["field"], field)

    def test_partial_update_keeps_other_fields(self):
        response = self._patch({"firstname": "Ann", "lastname": "Lee"})
        self.assertEqual(
            response.status_code, status.HTTP_204_NO_CONTENT, response.text
        )
        response = self._patch({"lastname": "Roe", "email": "ann@example.org"})
        self.assertEqual(
            response.status_code, status.HTTP_204_NO_CONTENT, response.text
        )
        self.assertEqual(self.guest.firstname, "Ann")
        self.assertEqual(self.guest.lastname, "Roe")
        self.assertEqual(self.guest.email, "ann@example.org")

    def test_null_clears_a_field(self):
        self.guest.write({"firstname": "Ann", "lastname": "Lee", "city": "Madrid"})
        response = self._patch({"city": None})
        self.assertEqual(
            response.status_code, status.HTTP_204_NO_CONTENT, response.text
        )
        self.assertFalse(self.guest.city)
        self.assertEqual(self.guest.firstname, "Ann")

    def test_empty_body_changes_nothing(self):
        self.guest.write({"firstname": "Ann"})
        response = self._patch({})
        self.assertEqual(
            response.status_code, status.HTTP_204_NO_CONTENT, response.text
        )
        self.assertEqual(self.guest.firstname, "Ann")
        self.assertFalse(self.guest.origin_input_data)

    def test_merge_patch_content_type_is_accepted(self):
        response = self._patch(
            {"firstname": "Ann"}, content_type="application/merge-patch+json"
        )
        self.assertEqual(
            response.status_code, status.HTTP_204_NO_CONTENT, response.text
        )
        self.assertEqual(self.guest.firstname, "Ann")

    def test_maps_ids_to_records(self):
        state = self.env["res.country.state"].search(
            [("country_id", "=", self.country_es.id)], limit=1
        )
        response = self._patch(
            {
                "gender": "male",
                "birthdate": "1985-03-02",
                "nationality": self.country_pt.id,
                "documentType": self.id_category.id,
                "documentNumber": "AB123",
                "documentCountry": self.country_pt.id,
                "country": self.country_es.id,
                "state": state.id,
                "city": "Madrid",
                "street": "Main 1",
                "zipCode": "28001",
            }
        )
        self.assertEqual(
            response.status_code, status.HTTP_204_NO_CONTENT, response.text
        )
        guest = self.guest
        self.assertEqual(guest.gender, "male")
        self.assertEqual(str(guest.birthdate_date), "1985-03-02")
        self.assertEqual(guest.nationality_id, self.country_pt)
        self.assertEqual(guest.document_type, self.id_category)
        self.assertEqual(guest.document_number, "AB123")
        self.assertEqual(guest.document_country_id, self.country_pt)
        self.assertEqual(guest.country_id, self.country_es)
        self.assertEqual(guest.state_id, state)
        self.assertEqual(guest.city, "Madrid")
        self.assertEqual(guest.street, "Main 1")
        self.assertEqual(guest.zip, "28001")
        self.assertEqual(guest.origin_input_data, "wizard")

    def test_null_clears_relational_fields(self):
        self.guest.write(
            {"nationality_id": self.country_pt.id, "document_type": self.id_category.id}
        )
        response = self._patch({"nationality": None, "documentType": None})
        self.assertEqual(
            response.status_code, status.HTTP_204_NO_CONTENT, response.text
        )
        self.assertFalse(self.guest.nationality_id)
        self.assertFalse(self.guest.document_type)

    def test_unknown_country_is_422(self):
        for field in ("nationality", "documentCountry", "country"):
            response = self._patch({"firstname": "Ann", field: 999_999_999})
            self._assert_problem(response, "/errors/unknown-country", field)
        self.assertFalse(self.guest.firstname)

    def test_unknown_document_type_is_422(self):
        response = self._patch({"documentType": 999_999_999})
        self._assert_problem(response, "/errors/unknown-document-type", "documentType")

    def test_unknown_state_is_422(self):
        response = self._patch({"country": self.country_es.id, "state": 999_999_999})
        self._assert_problem(response, "/errors/unknown-country-state", "state")

    def test_state_of_another_country_is_422(self):
        state = self.env["res.country.state"].create(
            {"name": "Test region", "code": "TRG", "country_id": self.country_fr.id}
        )
        response = self._patch({"country": self.country_pt.id, "state": state.id})
        self._assert_problem(response, "/errors/unknown-country-state", "state")

    def test_null_clears_the_state(self):
        state = self.env["res.country.state"].search(
            [("country_id", "=", self.country_es.id)], limit=1
        )
        self.guest.write({"country_id": self.country_es.id, "state_id": state.id})
        response = self._patch({"state": None})
        self.assertEqual(
            response.status_code, status.HTTP_204_NO_CONTENT, response.text
        )
        self.assertFalse(self.guest.state_id)

    def test_changing_the_country_drops_a_state_of_the_old_one(self):
        state = self.env["res.country.state"].create(
            {"name": "Test region", "code": "TRG", "country_id": self.country_fr.id}
        )
        self.guest.write({"country_id": self.country_fr.id, "state_id": state.id})
        response = self._patch({"country": self.country_pt.id})
        self.assertEqual(
            response.status_code, status.HTTP_204_NO_CONTENT, response.text
        )
        self.assertEqual(self.guest.country_id, self.country_pt)
        self.assertFalse(self.guest.state_id)

    def test_signature_is_stored(self):
        response = self._patch({"signature": _png_base64()})
        self.assertEqual(
            response.status_code, status.HTTP_204_NO_CONTENT, response.text
        )
        self.assertTrue(self.guest.signature)
        response = self._patch({"signature": None})
        self.assertEqual(
            response.status_code, status.HTTP_204_NO_CONTENT, response.text
        )
        self.assertFalse(self.guest.signature)

    def test_invalid_signature_is_422(self):
        response = self._patch({"signature": "not base64 !!"})
        self._assert_problem(response, "/errors/invalid-signature", "signature")

    def test_signature_that_is_not_an_image_is_422(self):
        response = self._patch({"signature": base64.b64encode(b"hello").decode()})
        self._assert_problem(response, "/errors/checkin-partner-invalid")

    def test_state_is_recomputed_and_the_guest_is_never_boarded(self):
        self.assertEqual(self.guest.state, "dummy")
        with mock.patch.object(PmsCheckinPartner, "action_on_board") as on_board:
            response = self._patch({"firstname": "Ann"})
            self.assertEqual(
                response.status_code, status.HTTP_204_NO_CONTENT, response.text
            )
            self.assertEqual(self.guest.state, "draft")
            response = self._patch(
                {
                    "lastname": "Lee",
                    "gender": "female",
                    "birthdate": "1990-05-17",
                    "nationality": self.country_pt.id,
                    "documentType": self.id_category.id,
                    "documentNumber": "AB123",
                    "documentCountry": self.country_pt.id,
                    "country": self.country_pt.id,
                    "city": "Lisbon",
                    "street": "Rua 1",
                    "zipCode": "1000",
                }
            )
            self.assertEqual(
                response.status_code, status.HTTP_204_NO_CONTENT, response.text
            )
            on_board.assert_not_called()
        self.assertEqual(self.guest.state, "precheckin")

    def test_validation_error_is_422_and_nothing_is_written(self):
        spanish_only = self.env["res.partner.id_category"].create(
            {
                "name": "Spanish only",
                "code": "SPN",
                "country_ids": [(6, 0, [self.country_es.id])],
            }
        )
        response = self._patch(
            {
                "firstname": "Ann",
                "documentType": spanish_only.id,
                "documentCountry": self.country_pt.id,
            }
        )
        self._assert_problem(response, "/errors/checkin-partner-invalid")
        self.assertTrue(response.json()["detail"])
        self.assertFalse(self.guest.document_type)

    def test_unsupported_fields_are_rejected(self):
        for key in ("acceptedTerms", "acceptedHouseRules", "marketingConsent"):
            response = self._patch({key: True})
            self.assertEqual(
                response.status_code,
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                response.text,
            )

    def test_invalid_gender_is_422(self):
        response = self._patch({"gender": "robot"})
        self.assertEqual(
            response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY, response.text
        )

    def test_partner_of_another_folio_returns_404(self):
        other_folio = self._create_folio()
        self._create_reservation(other_folio, self.room2, adults=1)
        response = self._patch({"firstname": "Ann"}, folio=other_folio)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.text)
        self.assertFalse(self.guest.firstname)

    def test_other_property_user_gets_404(self):
        self._create_other_property_user(same_company=True)
        with self._create_test_client() as test_client:
            self._login_other_user(test_client)
            response = test_client.patch(
                self._base_url(checkin_partner=self.guest), json={"firstname": "Ann"}
            )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.text)
        self.assertFalse(self.guest.firstname)

    def test_done_and_cancelled_partners_are_read_only(self):
        self.guest.write({"firstname": "Ann"})
        for state in ("done", "cancel"):
            self.guest.state = state
            for payload in ({"firstname": "Bob"}, {}):
                response = self._patch(payload)
                self.assertEqual(
                    response.status_code, status.HTTP_409_CONFLICT, response.text
                )
                self.assertEqual(
                    response.json()["type"], "/errors/checkin-partner-read-only"
                )
                self.assertEqual(self.guest.firstname, "Ann")
                self.assertFalse(self.guest.origin_input_data)

    def test_cancelled_reservation_makes_its_partners_read_only(self):
        self.reservation.action_cancel()
        response = self._patch({"firstname": "Bob"})
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT, response.text)

    def test_oversize_signature_is_422(self):
        response = self._patch({"signature": "A" * 500_001})
        self.assertEqual(
            response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY, response.text
        )
