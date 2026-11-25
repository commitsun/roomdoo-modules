from fastapi import status

from odoo.addons.pms_fastapi.tests.common import CommonTestCheckin

from .common import CommonTestPortalCheckinUpdate


class TestPortalCheckinUpdate(CommonTestPortalCheckinUpdate):
    def test_merge_patch_writes_and_clears(self):
        response = self._patch({"firstname": "Ann", "lastname": "Lee", "city": "Porto"})

        self.assertEqual(
            response.status_code, status.HTTP_204_NO_CONTENT, response.text
        )
        self.assertEqual(response.content, b"")
        self.assertEqual(
            (self.guest.firstname, self.guest.lastname, self.guest.city),
            ("Ann", "Lee", "Porto"),
        )
        self.assertEqual(self.guest.state, "draft")
        self.assertEqual(self.guest.origin_input_data, "wizard")

        response = self._patch({"city": None, "email": "ann@example.org"})

        self.assertEqual(
            response.status_code, status.HTTP_204_NO_CONTENT, response.text
        )
        self.assertFalse(self.guest.city)
        self.assertEqual(self.guest.firstname, "Ann")
        self.assertEqual(self.guest.email, "ann@example.org")

    def test_complete_data_completes_the_guest_and_creates_its_contact(self):
        response = self._patch(self._complete_data("Z999"))

        self.assertEqual(
            response.status_code, status.HTTP_204_NO_CONTENT, response.text
        )
        self.assertEqual(self.guest.state, "precheckin")
        self.assertEqual(self.guest.nationality_id, self.portugal)
        self.assertEqual(self.guest.document_type, self.category)
        self.assertEqual(self.guest.document_country_id, self.portugal)
        contact = self.guest.partner_id
        self.assertTrue(contact)
        self.assertEqual((contact.firstname, contact.lastname), ("Ann", "Lee"))
        self.assertEqual(contact.id_numbers.mapped("name"), ["Z999"])
        detail = self._get(self._url()).json()
        self.assertEqual(detail["documentNumber"], "Z999")
        self.assertEqual(
            detail["documentType"], {"id": self.category.id, "name": "Portal document"}
        )

    def test_staff_problems_are_answered(self):
        response = self._patch({"firstname": "Ann", "nationality": 999999999})

        body = self._assert_problem(
            response, status.HTTP_422_UNPROCESSABLE_ENTITY, "/errors/unknown-country"
        )
        self.assertEqual(body["field"], "nationality")
        self.assertFalse(self.guest.firstname)
        self.assertFalse(self.guest.origin_input_data)

    def test_invalid_data_is_rejected_and_nothing_is_written(self):
        spanish_only = self.env["res.partner.id_category"].create(
            {
                "name": "Spanish only",
                "code": "SPON",
                "country_ids": [(6, 0, [self.spain.id])],
            }
        )

        response = self._patch(
            {
                "firstname": "Ann",
                "documentType": spanish_only.id,
                "documentCountry": self.portugal.id,
            }
        )

        self._assert_problem(
            response, status.HTTP_422_UNPROCESSABLE_ENTITY, "/errors/guest-invalid"
        )
        self.assertFalse(self.guest.firstname)
        self.assertFalse(self.guest.document_type)

    def test_read_only_guests_are_409(self):
        self.guest.write({"firstname": "Ann"})
        for state in ("done", "cancel"):
            self.guest.state = state
            with self.subTest(state=state):
                response = self._patch({"firstname": "Bob"})

                self._assert_problem(
                    response, status.HTTP_409_CONFLICT, "/errors/guest-read-only"
                )
                self.assertEqual(self.guest.firstname, "Ann")
                self.assertFalse(self.guest.origin_input_data)

    def test_guests_out_of_the_folio_are_not_written(self):
        other = self._create_folio([(self.single, 5, 1, 1, 0)])
        other_guest = other.checkin_partner_ids
        folio = self._create_folio(
            [(self.double, 3, 2, 1, 0), (self.single, 3, 2, 1, 0)]
        )
        cancelled = folio.reservation_ids.filtered(
            lambda r: r.room_type_id == self.single
        )
        cancelled.action_cancel()
        for guest in (other_guest, cancelled.checkin_partner_ids):
            with self.subTest(guest=guest.id):
                response = self._patch({"firstname": "Mallory"}, guest, folio)

                self._assert_problem(
                    response, status.HTTP_404_NOT_FOUND, "/errors/guest-not-found"
                )
                self.assertFalse(guest.firstname)
                self.assertFalse(guest.origin_input_data)

    def test_the_contact_of_a_document_is_never_linked(self):
        contact = self._create_contact_with_document("X1234")

        response = self._patch(
            {
                "documentType": self.category.id,
                "documentNumber": "X1234",
                "documentCountry": self.portugal.id,
            }
        )

        self.assertEqual(
            response.status_code, status.HTTP_204_NO_CONTENT, response.text
        )
        self.assertFalse(self.guest.partner_id)
        self.assertEqual(self.guest.document_number, "X1234")
        self.assertEqual(
            self._get(self._url()).json(),
            {
                "documentType": {"id": self.category.id, "name": "Portal document"},
                "documentNumber": "X1234",
                "documentCountry": {
                    "id": self.portugal.id,
                    "name": self.portugal.name,
                },
            },
        )
        self.assertEqual(contact.id_numbers.mapped("name"), ["X1234"])

    def test_complete_data_with_the_document_of_a_contact_leaves_it_unlinked(self):
        contact = self._create_contact_with_document("X1234")
        partners_before = self.env["res.partner"].search_count([])

        response = self._patch(self._complete_data("X1234"))

        self.assertEqual(
            response.status_code, status.HTTP_204_NO_CONTENT, response.text
        )
        self.assertFalse(self.guest.partner_id)
        self.assertEqual(self.guest.state, "precheckin")
        self.assertEqual(self.env["res.partner"].search_count([]), partners_before)
        self.assertEqual(
            (contact.firstname, contact.email), ("Mallory", "mallory@example.org")
        )
        self.assertEqual(contact.id_numbers.mapped("name"), ["X1234"])
        detail = self._get(self._url()).json()
        self.assertEqual(detail["firstname"], "Ann")
        self.assertNotIn("email", detail)


class TestStaffCheckinLinksTheContactOfADocument(CommonTestCheckin):
    """The portal guard leaves the staff check-in as it was."""

    def test_staff_update_links_the_contact_of_the_document(self):
        contact = self.env["res.partner"].create(
            {
                "firstname": "Mallory",
                "lastname": "Stranger",
                "id_numbers": [
                    (
                        0,
                        0,
                        {
                            "name": "AB123",
                            "category_id": self.id_category.id,
                            "country_id": self.country_pt.id,
                        },
                    )
                ],
            }
        )
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.patch(
                self._base_url(checkin_partner=self.guest),
                json={
                    "documentType": self.id_category.id,
                    "documentNumber": "AB123",
                    "documentCountry": self.country_pt.id,
                },
            )

        self.assertEqual(
            response.status_code, status.HTTP_204_NO_CONTENT, response.text
        )
        self.assertEqual(self.guest.partner_id, contact)
        self.assertEqual(self.guest.firstname, "Mallory")

    def test_staff_update_reports_the_document_of_another_contact(self):
        """The staff check-in still rejects a document number held by another
        contact when the guest already has its own."""
        self.env["res.partner"].create(
            {
                "firstname": "Mallory",
                "lastname": "Stranger",
                "id_numbers": [
                    (
                        0,
                        0,
                        {
                            "name": "AB123",
                            "category_id": self.id_category.id,
                            "country_id": self.country_pt.id,
                        },
                    )
                ],
            }
        )
        own_contact = self.env["res.partner"].create(
            {"firstname": "Ann", "lastname": "Lee"}
        )
        self.guest.write(
            {
                "partner_id": own_contact.id,
                "document_type": self.id_category.id,
                "document_number": "OWN-1",
                "document_country_id": self.country_pt.id,
            }
        )
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.patch(
                self._base_url(checkin_partner=self.guest),
                json={"documentNumber": "AB123"},
            )

        self.assertEqual(
            response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY, response.text
        )
        self.assertEqual(response.json()["type"], "/errors/guest-invalid")
        self.assertEqual(self.guest.document_number, "OWN-1")
        self.assertEqual(own_contact.id_numbers.mapped("name"), ["OWN-1"])
