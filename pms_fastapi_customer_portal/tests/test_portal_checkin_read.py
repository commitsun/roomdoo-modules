from fastapi import status

from .common import CommonTestCustomerPortalApi


class TestPortalCheckinRead(CommonTestCustomerPortalApi):
    def setUp(self):
        super().setUp()
        self.folio = self._create_folio([(self.double, 3, 2, 2, 0)])
        self.reservation = self.folio.reservation_ids
        self.guests = self.reservation.checkin_partner_ids.sorted("id")
        self.guest = self.guests[0]
        self.base = f"/portal/{self.folio.access_token}"

    def test_guests_are_grouped_by_room(self):
        self.guest.write(
            {"firstname": "Ann", "lastname": "Lee", "birthdate_date": "1990-05-17"}
        )
        room = self.reservation.preferred_room_id

        response = self._get(f"{self.base}/checkin-guests")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        self.assertEqual(
            response.json(),
            {
                "rooms": [
                    {
                        "id": self.reservation.id,
                        "name": room.name if room else self.double.name,
                        "checkinPartners": [
                            {
                                "id": self.guest.id,
                                "state": "pending",
                                "displayName": self.guest.name,
                                "birthdate": "1990-05-17",
                            },
                            {
                                "id": self.guests[1].id,
                                "state": "pending",
                                "displayName": None,
                                "birthdate": None,
                            },
                        ],
                    }
                ]
            },
        )

    def test_guests_of_a_cancelled_reservation_are_not_listed(self):
        folio = self._create_folio(
            [(self.double, 3, 2, 1, 0), (self.single, 3, 2, 1, 0)]
        )
        cancelled = folio.reservation_ids.filtered(
            lambda r: r.room_type_id == self.single
        )
        cancelled.action_cancel()

        response = self._get(f"/portal/{folio.access_token}/checkin-guests")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        self.assertEqual(
            [room["id"] for room in response.json()["rooms"]],
            (folio.reservation_ids - cancelled).ids,
        )

    def test_context_comes_from_the_property(self):
        self.property.country_id = self.env.ref("base.es")

        response = self._get(f"{self.base}/checkin-context")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        body = response.json()
        self.assertEqual(body["localization"], "es")
        self.assertEqual(body["hotelName"], "Hotel Portal")
        self.assertEqual(body["arrivalDate"], self.folio.first_checkin.isoformat())
        self.assertIs(body["ocrAvailable"], False)

    def test_context_says_when_the_property_scans_documents(self):
        self._enable_test_ocr_supplier()

        response = self._get(f"{self.base}/checkin-context")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        self.assertIs(response.json()["ocrAvailable"], True)

    def test_guest_detail(self):
        spain = self.env.ref("base.es")
        category = self.env["res.partner.id_category"].create(
            {"name": "Portal document", "code": "PDOC"}
        )
        self.guest.write(
            {
                "firstname": "Ann",
                "lastname": "Lee",
                "nationality_id": spain.id,
                "document_type": category.id,
                "document_number": "X1234",
                "document_country_id": spain.id,
                "email": "ann@example.org",
            }
        )

        response = self._get(f"{self.base}/checkin-partners/{self.guest.id}")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        self.assertEqual(
            response.json(),
            {
                "firstname": "Ann",
                "lastname": "Lee",
                "nationality": {"id": spain.id, "name": spain.name},
                "documentType": {"id": category.id, "name": "Portal document"},
                "documentNumber": "X1234",
                "documentCountry": {"id": spain.id, "name": spain.name},
                "email": "ann@example.org",
            },
        )

    def test_empty_guest_detail(self):
        response = self._get(f"{self.base}/checkin-partners/{self.guests[1].id}")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        self.assertEqual(response.json(), {})


class TestPortalCheckinGuestScope(CommonTestCustomerPortalApi):
    """A token reaches only the guests of the live stays of its folio."""

    def setUp(self):
        super().setUp()
        self.folio = self._create_folio(
            [(self.double, 3, 2, 1, 0), (self.single, 3, 2, 1, 0)]
        )
        self.base = f"/portal/{self.folio.access_token}"
        other = self._create_folio([(self.double, 5, 1, 1, 0)])
        self.other_guest = other.reservation_ids.checkin_partner_ids
        cancelled = self.folio.reservation_ids.filtered(
            lambda r: r.room_type_id == self.single
        )
        cancelled.action_cancel()
        self.cancelled_guest = cancelled.checkin_partner_ids
        self.assertEqual(self.folio.state, "confirm")

    def _assert_guest_not_found(self, response):
        body = self._assert_problem(
            response, status.HTTP_404_NOT_FOUND, "/errors/guest-not-found"
        )
        self.assertEqual(body["title"], "Guest not found")
        self.assertEqual(body["detail"], "The guest is not part of this booking.")

    def test_guest_of_another_folio_is_not_found(self):
        response = self._get(f"{self.base}/checkin-partners/{self.other_guest.id}")

        self._assert_guest_not_found(response)

    def test_guest_of_a_cancelled_reservation_is_not_found(self):
        response = self._get(f"{self.base}/checkin-partners/{self.cancelled_guest.id}")

        self._assert_guest_not_found(response)

    def test_unknown_guest_is_not_found(self):
        response = self._get(f"{self.base}/checkin-partners/999999999")

        self._assert_guest_not_found(response)


class TestPortalCheckinCatalogs(CommonTestCustomerPortalApi):
    def setUp(self):
        super().setUp()
        self.folio = self._create_folio([(self.double, 3, 2, 1, 0)])
        self.base = f"/portal/{self.folio.access_token}"
        self.spain = self.env.ref("base.es")
        self.portugal = self.env.ref("base.pt")

    def test_countries(self):
        response = self._get(f"{self.base}/countries")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        self.assertEqual(
            response.json(),
            [
                {"id": country.id, "name": country.name, "code": country.code}
                for country in self.env["res.country"].search([])
            ],
        )

    def test_country_states_of_a_country(self):
        response = self._get(f"{self.base}/country-states?country={self.spain.id}")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        states = self.env["res.country.state"].search(
            [("country_id", "=", self.spain.id)]
        )
        self.assertTrue(states)
        self.assertEqual(
            response.json(),
            [
                {
                    "id": state.id,
                    "name": state.name,
                    "country": {"id": self.spain.id, "name": self.spain.name},
                }
                for state in states
            ],
        )

    def test_country_states_of_every_country(self):
        response = self._get(f"{self.base}/country-states")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        self.assertEqual(
            [item["id"] for item in response.json()],
            self.env["res.country.state"].search([]).ids,
        )

    def test_id_number_categories_of_a_country(self):
        categories = self.env["res.partner.id_category"]
        anywhere = categories.create({"name": "Anywhere", "code": "ANYW"})
        spanish = categories.create(
            {
                "name": "Spanish only",
                "code": "SPON",
                "country_ids": [(6, 0, [self.spain.id])],
            }
        )
        portuguese = categories.create(
            {
                "name": "Portuguese only",
                "code": "PTON",
                "country_ids": [(6, 0, [self.portugal.id])],
            }
        )

        response = self._get(
            f"{self.base}/id-number-categories?country={self.spain.id}"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        body = {item["id"]: item for item in response.json()}
        self.assertNotIn(portuguese.id, body)
        self.assertEqual(
            body[anywhere.id],
            {
                "id": anywhere.id,
                "name": "Anywhere",
                "code": "ANYW",
                "isValidableDocument": False,
                "countries": [],
            },
        )
        self.assertEqual(
            body[spanish.id]["countries"],
            [{"id": self.spain.id, "name": self.spain.name}],
        )

    def test_id_number_categories_of_every_country(self):
        response = self._get(f"{self.base}/id-number-categories")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        self.assertEqual(
            [item["id"] for item in response.json()],
            self.env["res.partner.id_category"].search([]).ids,
        )


class TestPortalCheckinHelper(CommonTestCustomerPortalApi):
    def test_the_portal_helper_extends_the_staff_one(self):
        """Every module that extends the staff check-in helper, as a
        localization does, extends the portal one as well."""
        staff = self.env["pms_api_checkin.checkin_router.helper"]
        portal = self.env["pms_fastapi_customer_portal.checkin_router.helper"]

        self.assertIsInstance(portal, type(staff))
        self.assertNotEqual(portal._name, staff._name)
