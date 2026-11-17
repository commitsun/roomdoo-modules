from unittest import mock

from fastapi import status

from odoo.addons.pms.models.pms_property import PmsProperty
from odoo.addons.pms_fastapi.tests.common import CommonTestCheckin


class TestCheckinL10nEs(CommonTestCheckin):
    def _get(self, url):
        with self._create_test_client() as test_client:
            self._login(test_client)
            return test_client.get(url)

    def _patch(self, payload, checkin_partner=None):
        url = self._base_url(checkin_partner=checkin_partner or self.guest)
        with self._create_test_client() as test_client:
            self._login(test_client)
            return test_client.patch(url, json=payload)

    def _assert_problem(self, response, type_, field=None):
        self.assertEqual(
            response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY, response.text
        )
        self.assertEqual(response.json()["type"], type_)
        if field:
            self.assertEqual(response.json()["field"], field)

    def test_context_reports_the_unaccompanied_minors(self):
        for value in (True, False):
            self.folio.ses_unaccompanied_minors = value
            response = self._get(f"/folios/{self.folio.id}/checkin-context")
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            self.assertIs(response.json()["unaccompaniedMinors"], value)

    def test_detail_inverts_the_stored_relationship(self):
        responsible, minor = self.checkin_partners
        minor.write(
            {
                "ses_partners_relationship": "HJ",
                "ses_related_checkin_partner_id": responsible.id,
            }
        )
        response = self._get(self._base_url(checkin_partner=minor))
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        self.assertEqual(response.json()["relationship"], "PM")
        self.assertEqual(response.json()["responsibleCheckinPartnerId"], responsible.id)

    def test_detail_returns_the_support_number(self):
        self.guest.support_number = "SUP1"
        response = self._get(self._base_url(checkin_partner=self.guest))
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        self.assertEqual(response.json()["documentSupportNumber"], "SUP1")

    def test_support_number_is_stored_and_cleared(self):
        response = self._patch({"documentSupportNumber": "SUP1"})
        self.assertEqual(
            response.status_code, status.HTTP_204_NO_CONTENT, response.text
        )
        self.assertEqual(self.guest.support_number, "SUP1")
        response = self._patch({"documentSupportNumber": None})
        self.assertEqual(
            response.status_code, status.HTTP_204_NO_CONTENT, response.text
        )
        self.assertFalse(self.guest.support_number)

    def test_scan_returns_the_support_number(self):
        self._enable_test_ocr_supplier()
        with (
            mock.patch.object(
                PmsProperty,
                "_test_document_process",
                create=True,
                return_value={"document_support_number": "SUP1"},
            ),
            self._create_test_client() as test_client,
        ):
            self._login(test_client)
            response = test_client.post(
                f"{self._base_url(checkin_partner=self.guest)}/document-scan",
                json={"front": "ZnJvbnQ="},
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        self.assertEqual(response.json(), {"documentSupportNumber": "SUP1"})

    def test_a_state_of_the_old_country_is_not_dropped_for_a_spanish_resident(self):
        state = self.env["res.country.state"].create(
            {"name": "Test region", "code": "TRG", "country_id": self.country_fr.id}
        )
        self.guest.write({"country_id": self.country_fr.id, "state_id": state.id})
        response = self._patch({"country": self.country_es.id})
        self.assertEqual(
            response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY, response.text
        )
        self.assertEqual(self.guest.country_id, self.country_fr)
        self.assertEqual(self.guest.state_id, state)

    def test_relationship_and_responsible(self):
        responsible, minor = self.checkin_partners
        response = self._patch(
            {"relationship": "PM", "responsibleCheckinPartnerId": responsible.id},
            checkin_partner=minor,
        )
        self.assertEqual(
            response.status_code, status.HTTP_204_NO_CONTENT, response.text
        )
        # Stored from the guest's own perspective.
        self.assertEqual(minor.ses_partners_relationship, "HJ")
        self.assertEqual(minor.ses_related_checkin_partner_id, responsible)
        # The responsible adult is left untouched.
        self.assertFalse(responsible.ses_partners_relationship)
        with self._create_test_client() as test_client:
            self._login(test_client)
            detail = test_client.get(self._base_url(checkin_partner=minor))
        self.assertEqual(detail.json()["relationship"], "PM")
        self.assertEqual(detail.json()["responsibleCheckinPartnerId"], responsible.id)

    def test_null_clears_relationship_and_responsible(self):
        responsible, minor = self.checkin_partners
        minor.write(
            {
                "ses_partners_relationship": "HJ",
                "ses_related_checkin_partner_id": responsible.id,
            }
        )
        response = self._patch(
            {"relationship": None, "responsibleCheckinPartnerId": None},
            checkin_partner=minor,
        )
        self.assertEqual(
            response.status_code, status.HTTP_204_NO_CONTENT, response.text
        )
        self.assertFalse(minor.ses_partners_relationship)
        self.assertFalse(minor.ses_related_checkin_partner_id)

    def test_responsible_of_another_folio_is_422(self):
        other_folio = self._create_folio()
        other = self._create_reservation(other_folio, self.room2, adults=1)
        response = self._patch(
            {"responsibleCheckinPartnerId": other.checkin_partner_ids.id}
        )
        self._assert_problem(
            response, "/errors/unknown-responsible", "responsibleCheckinPartnerId"
        )

    def test_the_guest_cannot_be_its_own_responsible(self):
        response = self._patch({"responsibleCheckinPartnerId": self.guest.id})
        self._assert_problem(response, "/errors/unknown-responsible")

    def test_non_numeric_responsible_is_422(self):
        response = self._patch({"responsibleCheckinPartnerId": "abc"})
        self.assertEqual(
            response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY, response.text
        )

    def test_unknown_relationship_is_422(self):
        response = self._patch({"relationship": "XX"})
        self._assert_problem(response, "/errors/unknown-relationship", "relationship")
