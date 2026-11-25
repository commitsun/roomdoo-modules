import datetime
import json
from unittest import mock

from fastapi import status

from odoo.exceptions import UserError

from odoo.addons.pms.models.pms_property import PmsProperty

from .common import CommonTestCustomerPortalApi

FRONT = "ZnJvbnQ="


class TestPortalCheckinDocumentScan(CommonTestCustomerPortalApi):
    def setUp(self):
        super().setUp()
        self.folio = self._create_folio([(self.double, 3, 2, 2, 0)])
        self.guest = self.folio.checkin_partner_ids.sorted("id")[0]
        self.portugal = self.env.ref("base.pt")
        self.category = self.env["res.partner.id_category"].create(
            {"name": "Portal document", "code": "PDOC"}
        )

    def _post(self, guest=None, folio=None, payload=None):
        folio = folio or self.folio
        guest = guest or self.guest
        return self._request(
            "POST",
            f"/portal/{folio.access_token}/checkin-partners/{guest.id}/document-scan",
            json=payload or {"front": FRONT},
        )

    def _patch_ocr(self, **kwargs):
        return mock.patch.object(
            PmsProperty, "_test_document_process", create=True, **kwargs
        )

    def _assert_scan_limit(self, response):
        body = self._assert_problem(
            response, status.HTTP_429_TOO_MANY_REQUESTS, "/errors/document-scan-limit"
        )
        self.assertEqual(body["title"], "Document scan limit reached")
        self.assertEqual(
            body["detail"], "No more documents can be scanned for this guest."
        )
        self.assertEqual(body["maxScans"], 5)

    def test_scan_maps_the_result_and_saves_nothing_but_the_count(self):
        self._enable_test_ocr_supplier()
        result = {
            "firstname": "Ann",
            "lastname": "Lee",
            "gender": "female",
            "birthdate": datetime.datetime(1990, 5, 17),
            "nationality": self.portugal.id,
            "document_type": self.category.id,
            "document_number": "AB123",
            "document_country_id": self.portugal.id,
        }
        with self._patch_ocr(return_value=result) as process:
            response = self._post()

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        portugal = {"id": self.portugal.id, "name": self.portugal.name}
        self.assertEqual(
            response.json(),
            {
                "firstname": "Ann",
                "lastname": "Lee",
                "gender": "female",
                "birthdate": "1990-05-17",
                "nationality": portugal,
                "documentType": {"id": self.category.id, "name": "Portal document"},
                "documentNumber": "AB123",
                "documentCountry": portugal,
            },
        )
        process.assert_called_once_with(FRONT, False)
        self.assertEqual(self.guest.portal_document_scan_count, 1)
        self.assertFalse(self.guest.firstname)
        self.assertEqual(self.guest.state, "dummy")

    def test_five_scans_then_429_without_calling_the_ocr(self):
        self._enable_test_ocr_supplier()
        with self._patch_ocr(return_value={"firstname": "Ann"}) as process:
            for attempt in range(1, 6):
                response = self._post()
                self.assertEqual(response.status_code, status.HTTP_200_OK, attempt)
                self.assertEqual(self.guest.portal_document_scan_count, attempt)

            response = self._post()

        self._assert_scan_limit(response)
        self.assertEqual(process.call_count, 5)
        self.assertEqual(self.guest.portal_document_scan_count, 5)
        context = self._get(f"/portal/{self.folio.access_token}/checkin-context")
        self.assertIs(context.json()["ocrAvailable"], True)

    def test_the_limit_is_per_guest(self):
        self._enable_test_ocr_supplier()
        self.guest.portal_document_scan_count = 5
        other_guest = self.folio.checkin_partner_ids.sorted("id")[1]
        with self._patch_ocr(return_value={"firstname": "Ann"}) as process:
            limited = self._post()
            response = self._post(other_guest)

        self._assert_scan_limit(limited)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        process.assert_called_once_with(FRONT, False)
        self.assertEqual(other_guest.portal_document_scan_count, 1)

    def test_unreadable_and_failed_scans_count(self):
        self._enable_test_ocr_supplier()
        with self._patch_ocr(return_value={}):
            unreadable = self._post()
        with self._patch_ocr(side_effect=UserError("Supplier down")):
            failed = self._post()

        for response in (unreadable, failed):
            self._assert_problem(
                response,
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "/errors/document-not-readable",
            )
        self.assertEqual(self.guest.portal_document_scan_count, 2)

    def test_scan_without_ocr_is_the_staff_422_and_counts(self):
        response = self._post()

        self._assert_problem(
            response, status.HTTP_422_UNPROCESSABLE_ENTITY, "/errors/ocr-not-available"
        )
        self.assertEqual(self.guest.portal_document_scan_count, 1)

    def test_read_only_guest_is_409_and_not_counted(self):
        self._enable_test_ocr_supplier()
        self.guest.state = "done"
        with self._patch_ocr(return_value={"firstname": "Ann"}) as process:
            response = self._post()

        self._assert_problem(
            response, status.HTTP_409_CONFLICT, "/errors/guest-read-only"
        )
        process.assert_not_called()
        self.assertEqual(self.guest.portal_document_scan_count, 0)

    def test_guests_out_of_the_folio_are_not_scanned(self):
        self._enable_test_ocr_supplier()
        other_guest = self._create_folio(
            [(self.single, 5, 1, 1, 0)]
        ).checkin_partner_ids
        folio = self._create_folio(
            [(self.double, 3, 2, 1, 0), (self.single, 3, 2, 1, 0)]
        )
        cancelled = folio.reservation_ids.filtered(
            lambda r: r.room_type_id == self.single
        )
        cancelled.action_cancel()
        for guest in (other_guest, cancelled.checkin_partner_ids):
            with self.subTest(guest=guest.id):
                with self._patch_ocr(return_value={"firstname": "Ann"}) as process:
                    response = self._post(guest, folio)

                self._assert_problem(
                    response, status.HTTP_404_NOT_FOUND, "/errors/guest-not-found"
                )
                process.assert_not_called()
                self.assertEqual(guest.portal_document_scan_count, 0)

    def test_counting_a_scan_does_not_link_the_contact_of_the_document(self):
        self.env["res.partner"].create(
            {
                "firstname": "Mallory",
                "lastname": "Stranger",
                "id_numbers": [
                    (
                        0,
                        0,
                        {
                            "name": "X1234",
                            "category_id": self.category.id,
                            "country_id": self.portugal.id,
                        },
                    )
                ],
            }
        )
        url = f"/portal/{self.folio.access_token}/checkin-partners/{self.guest.id}"
        patched = self._request(
            "PATCH",
            url,
            content=json.dumps(
                {"documentType": self.category.id, "documentNumber": "X1234"}
            ),
            headers={"Content-Type": "application/merge-patch+json"},
        )
        self.assertEqual(patched.status_code, status.HTTP_204_NO_CONTENT, patched.text)
        self._enable_test_ocr_supplier()

        with self._patch_ocr(return_value={"firstname": "Ann"}):
            response = self._post()

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        self.assertEqual(self.guest.portal_document_scan_count, 1)
        self.assertFalse(self.guest.partner_id)
        self.assertFalse(self.guest.firstname)

    def test_front_is_required(self):
        response = self._post(payload={"back": "YmFjaw=="})

        self.assertEqual(
            response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY, response.text
        )
        self.assertEqual(self.guest.portal_document_scan_count, 0)
