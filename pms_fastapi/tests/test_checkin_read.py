from fastapi import status

from .common import CommonTestCheckin


class TestCheckinReadEndpoints(CommonTestCheckin):
    def _get(self, url):
        with self._create_test_client() as test_client:
            self._login(test_client)
            return test_client.get(url)

    # checkin-guests

    def test_guests_group_by_reservation(self):
        reservation2 = self._create_reservation(self.folio, self.room2, adults=1)
        self.guest.write({"firstname": "Ann", "lastname": "Lee"})
        self.guest.write({"birthdate_date": "1990-05-17"})
        response = self._get(f"/folios/{self.folio.id}/checkin-guests")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        rooms = response.json()["rooms"]
        self.assertEqual(len(rooms), 2)
        by_id = {room["id"]: room for room in rooms}
        room = by_id[self.reservation.id]
        self.assertEqual(room["name"], "101")
        self.assertEqual(
            [cp["id"] for cp in room["checkinPartners"]],
            self.checkin_partners.ids,
        )
        first = room["checkinPartners"][0]
        self.assertEqual(first["displayName"], self.guest.name)
        self.assertEqual(first["birthdate"], "1990-05-17")
        self.assertEqual(first["state"], "pending")
        dummy = room["checkinPartners"][1]
        self.assertEqual(dummy["state"], "pending")
        self.assertIsNone(dummy["displayName"])
        self.assertIsNone(dummy["birthdate"])
        room2 = by_id[reservation2.id]
        self.assertEqual(room2["name"], "102")
        self.assertEqual(len(room2["checkinPartners"]), 1)

    def test_guest_states_are_mapped_to_the_api_states(self):
        states = {
            "dummy": "pending",
            "draft": "pending",
            "precheckin": "completed",
            "onboard": "inHouse",
            "done": "checkedOut",
        }
        for stored, expected in states.items():
            self.guest.state = stored
            response = self._get(f"/folios/{self.folio.id}/checkin-guests")
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            guests = response.json()["rooms"][0]["checkinPartners"]
            self.assertEqual(guests[0]["state"], expected, stored)

    def test_cancelled_guests_are_not_listed(self):
        self.guest.state = "cancel"
        response = self._get(f"/folios/{self.folio.id}/checkin-guests")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        guests = response.json()["rooms"][0]["checkinPartners"]
        self.assertEqual([g["id"] for g in guests], [self.checkin_partners[1].id])

    def test_cancelled_reservations_are_not_listed(self):
        folio = self._create_folio()
        reservation = self._create_reservation(folio, self.room2, adults=1)
        reservation.action_cancel()
        response = self._get(f"/folios/{folio.id}/checkin-guests")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        self.assertEqual(response.json(), {"rooms": []})

    def test_a_room_left_without_guests_is_dropped(self):
        self.checkin_partners.write({"state": "cancel"})
        response = self._get(f"/folios/{self.folio.id}/checkin-guests")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        self.assertEqual(response.json(), {"rooms": []})

    def test_out_of_service_reservation_has_no_guests(self):
        folio = self._create_folio()
        reservation = self._create_reservation(folio, self.room2, adults=1)
        reservation.write({"reservation_type": "out"})
        response = self._get(f"/folios/{folio.id}/checkin-guests")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        self.assertEqual(response.json(), {"rooms": []})

    def test_guests_without_room_use_the_reservation(self):
        folio = self._create_folio()
        reservation = self._create_reservation(folio, self.room2, adults=1)
        reservation.preferred_room_id = False
        if reservation.preferred_room_id:
            self.skipTest("The reservation is assigned a room automatically.")
        response = self._get(f"/folios/{folio.id}/checkin-guests")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        room = response.json()["rooms"][0]
        self.assertEqual(room["id"], reservation.id)
        self.assertEqual(room["name"], self.room_type.name)

    def test_guests_unknown_folio_returns_404(self):
        response = self._get("/folios/0/checkin-guests")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.text)

    def _assert_other_user_denied(self, expected_status):
        with self._create_test_client() as test_client:
            self._login_other_user(test_client)
            for url in (
                f"/folios/{self.folio.id}/checkin-guests",
                f"/folios/{self.folio.id}/checkin-context",
                self._base_url(checkin_partner=self.guest),
            ):
                response = test_client.get(url)
                self.assertEqual(response.status_code, expected_status, response.text)

    def test_other_company_user_gets_404(self):
        self._create_other_property_user()
        self._assert_other_user_denied(status.HTTP_404_NOT_FOUND)

    def test_other_property_user_of_same_company_gets_404(self):
        self._create_other_property_user(same_company=True)
        self._assert_other_user_denied(status.HTTP_404_NOT_FOUND)

    # checkin-partners/{id}

    def test_partner_detail_empty_guest(self):
        response = self._get(self._base_url(checkin_partner=self.guest))
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        self.assertEqual(response.json(), {})

    def test_partner_detail_maps_fields(self):
        state = self.env["res.country.state"].search(
            [("country_id", "=", self.country_es.id)], limit=1
        )
        self.guest.write(
            {
                "firstname": "Ann",
                "lastname": "Lee",
                "lastname2": "Ray",
                "gender": "female",
                "birthdate_date": "1990-05-17",
                "nationality_id": self.country_pt.id,
                "document_type": self.id_category.id,
                "document_number": "AB123",
                "document_country_id": self.country_pt.id,
                "country_id": self.country_es.id,
                "state_id": state.id,
                "city": "Madrid",
                "street": "Main 1",
                "zip": "28001",
                "email": "ann@example.org",
                "mobile": "+34600000000",
            }
        )
        response = self._get(self._base_url(checkin_partner=self.guest))
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        self.assertEqual(
            self._id_names(response.json()),
            {
                "firstname": "Ann",
                "lastname": "Lee",
                "lastname2": "Ray",
                "gender": "female",
                "birthdate": "1990-05-17",
                "nationality": {
                    "id": self.country_pt.id,
                    "name": self.country_pt.name,
                },
                "documentType": {
                    "id": self.id_category.id,
                    "name": self.id_category.name,
                },
                "documentNumber": "AB123",
                "documentCountry": {
                    "id": self.country_pt.id,
                    "name": self.country_pt.name,
                },
                "country": {
                    "id": self.country_es.id,
                    "name": self.country_es.name,
                },
                "state": {"id": state.id, "name": state.name},
                "city": "Madrid",
                "street": "Main 1",
                "zipCode": "28001",
                "email": "ann@example.org",
                "mobile": "+34600000000",
            },
        )
        self.assertNotIn("signature", response.json())

    def test_partner_of_another_folio_returns_404(self):
        other_folio = self._create_folio()
        self._create_reservation(other_folio, self.room2, adults=1)
        response = self._get(
            self._base_url(folio=other_folio, checkin_partner=self.guest)
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.text)

    # checkin-context

    def test_context_values(self):
        response = self._get(f"/folios/{self.folio.id}/checkin-context")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        body = response.json()
        self.assertEqual(
            {k: body[k] for k in ("localization", "hotelName", "ocrAvailable")},
            {"localization": "es", "hotelName": "Property 1", "ocrAvailable": False},
        )
        self.assertEqual(body["arrivalDate"], self.folio.first_checkin.isoformat())

    def test_context_sends_no_policy_links(self):
        response = self._get(f"/folios/{self.folio.id}/checkin-context")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        body = response.json()
        for key in ("termsUrl", "houseRulesUrl", "privacyPolicyUrl"):
            self.assertNotIn(key, body)

    def test_context_ocr_and_missing_country(self):
        self._enable_test_ocr_supplier()
        self.test_property.country_id = False
        response = self._get(f"/folios/{self.folio.id}/checkin-context")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        self.assertTrue(response.json()["ocrAvailable"])
        self.assertIsNone(response.json()["localization"])
