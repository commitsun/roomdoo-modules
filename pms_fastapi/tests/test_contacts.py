from fastapi import status

from odoo.addons.pms_fastapi.tests.common import CommonTestPmsApi


class TestContactsEndpoints(CommonTestPmsApi):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.test_partner = cls.env["res.partner"].create(
            {
                "firstname": "john",
                "lastname": "doe",
            }
        )
        cls.test_company_partner = cls.env["res.partner"].create(
            {
                "name": "Grand Hotel Group SL",
                "is_company": True,
            }
        )
        # Prepare reusable relational records
        cls.country = cls.env["res.country"].search(
            [("code", "=", "ES")], limit=1
        ) or cls.env["res.country"].create({"name": "Spain", "code": "ES"})
        cls.state = cls.env["res.country.state"].search(
            [
                ("code", "=", "M"),
                ("country_id", "=", cls.country.id),
            ],
            limit=1,
        ) or cls.env["res.country.state"].create(
            {
                "name": "Madrid",
                "code": "M",
                "country_id": cls.country.id,
            }
        )
        cls.nationality = cls.env["res.country"].search(
            [("code", "=", "FR")], limit=1
        ) or cls.env["res.country"].create({"name": "France", "code": "FR"})
        cls.payment_term = cls.env["account.payment.term"].search(
            [("name", "=", "30 Days")], limit=1
        ) or cls.env["account.payment.term"].create(
            {
                "name": "30 Days",
                "line_ids": [(0, 0, {"value": "balance", "days": 30})],
            }
        )
        cls.pricelist = cls.env["product.pricelist"].search(
            [("name", "=", "Public Pricelist")], limit=1
        ) or cls.env["product.pricelist"].create({"name": "Public Pricelist"})
        cls.tag1 = cls.env["res.partner.category"].search(
            [("name", "=", "VIP")], limit=1
        ) or cls.env["res.partner.category"].create({"name": "VIP"})
        cls.tag2 = cls.env["res.partner.category"].search(
            [("name", "=", "Newsletter")], limit=1
        ) or cls.env["res.partner.category"].create({"name": "Newsletter"})

        # Alternative set for PATCH updates
        cls.country2 = cls.env["res.country"].search(
            [("code", "=", "US")], limit=1
        ) or cls.env["res.country"].create({"name": "United States", "code": "US"})
        cls.state2 = cls.env["res.country.state"].search(
            [
                ("code", "=", "CA"),
                ("country_id", "=", cls.country2.id),
            ],
            limit=1,
        ) or cls.env["res.country.state"].create(
            {
                "name": "California",
                "code": "CA",
                "country_id": cls.country2.id,
            }
        )
        cls.nationality2 = cls.env["res.country"].search(
            [("code", "=", "IT")], limit=1
        ) or cls.env["res.country"].create({"name": "Italy", "code": "IT"})
        cls.payment_term2 = cls.env["account.payment.term"].search(
            [("name", "=", "Immediate")], limit=1
        ) or cls.env["account.payment.term"].create(
            {
                "name": "Immediate",
                "line_ids": [(0, 0, {"value": "balance", "days": 0})],
            }
        )
        cls.pricelist2 = cls.env["product.pricelist"].search(
            [("name", "=", "Corporate")], limit=1
        ) or cls.env["product.pricelist"].create({"name": "Corporate"})
        cls.tag_patch = cls.env["res.partner.category"].search(
            [("name", "=", "Corporate")], limit=1
        ) or cls.env["res.partner.category"].create({"name": "Corporate"})

    def test_contacts_get(self):
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.get("/contacts")
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            self.assertIn("count", response.json())
            self.assertIn("items", response.json())

    def test_contact_detail_get(self):
        """A person shows their given name in name and their last names apart."""
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.get(f"/contacts/{self.test_partner.id}")
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            self.assertEqual(response.json()["contactType"], "person")
            self.assertEqual(response.json()["name"], "john")
            self.assertEqual(response.json()["lastname"], "doe")
            self.assertNotIn("firstname", response.json())

    def test_contact_detail_get_company(self):
        """A company shows its whole name in name and never a last name."""
        self.assertTrue(self.test_company_partner.lastname)
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.get(f"/contacts/{self.test_company_partner.id}")
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            self.assertEqual(response.json()["contactType"], "company")
            self.assertEqual(response.json()["name"], "Grand Hotel Group SL")
            self.assertEqual(response.json()["lastname"], "")

    def test_contact_list_returns_the_full_name(self):
        """Listings keep returning the full name, unlike the detail."""
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.get("/contacts", params={"globalSearch": "doe"})
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            names = {item["name"] for item in response.json()["items"]}
            self.assertIn(self.test_partner.display_name, names)
            self.assertNotIn("john", names)

    def test_contact_detail_get_single_word_name(self):
        """A person whose stored name has no given name part: nothing invented.

        Reposting the same payload must not be rejected either.
        """
        partner = self.env["res.partner"].create({"name": "Madonna"})
        self.assertFalse(partner.firstname)
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.get(f"/contacts/{partner.id}")
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            self.assertEqual(response.json()["name"], "")
            self.assertEqual(response.json()["lastname"], "Madonna")
            response = test_client.patch(
                f"/contacts/{partner.id}",
                json={"name": "", "lastname": "Madonna"},
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)

    def test_contact_detail_get_company_with_person_name_parts(self):
        """A company whose stored parts are person-shaped still shows one name.

        Writing the name normalizes what is stored.
        """
        partner = self.env["res.partner"].create(
            {
                "firstname": "Grand",
                "lastname": "Resort SL",
                "is_company": True,
            }
        )
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.get(f"/contacts/{partner.id}")
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            self.assertEqual(response.json()["name"], "Grand Resort SL")
            self.assertEqual(response.json()["lastname"], "")
            response = test_client.patch(
                f"/contacts/{partner.id}",
                json={"name": "Grand Resort SL"},
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        self.env.invalidate_all()
        self.assertFalse(partner.firstname)
        self.assertEqual(partner.name, "Grand Resort SL")

    def test_contact_post(self):
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.post(
                "/contacts",
                json={
                    "lastname": "doe",
                    "name": "john",
                    "contactType": "person",
                    "phones": [
                        {"type": "phone", "number": "+34 911 111 111"},
                        {"type": "mobile", "number": "+34 622 222 222"},
                    ],
                    "nationality": self.nationality.id,
                    "state": self.state.id,
                    "country": self.country.id,
                    "paymentTerm": self.payment_term.id,
                    "pricelist": self.pricelist.id,
                    "tags": [self.tag1.id, self.tag2.id],
                },
            )
            self.assertEqual(
                response.status_code, status.HTTP_201_CREATED, response.text
            )
            self.assertIn("id", response.json())
            self.assertEqual(response.json()["lastname"], "doe")
            self.assertEqual(response.json()["name"], "john")
            self.assertIn("phones", response.json())
            self.assertEqual(len(response.json()["phones"]), 2)
            # Relational fields returned as nested objects
            self.assertEqual(
                response.json().get("nationality"),
                {"id": self.nationality.id, "name": self.nationality.name},
            )
            self.assertEqual(
                response.json().get("state"),
                {"id": self.state.id, "name": self.state.name},
            )
            self.assertEqual(
                response.json().get("country"),
                {"id": self.country.id, "name": self.country.name},
            )
            self.assertEqual(
                response.json().get("paymentTerm"),
                {"id": self.payment_term.id, "name": self.payment_term.name},
            )
            self.assertEqual(
                response.json().get("pricelist"),
                {"id": self.pricelist.id, "name": self.pricelist.name},
            )
            resp_tags = response.json().get("tags", [])
            self.assertEqual({t["id"] for t in resp_tags}, {self.tag1.id, self.tag2.id})
            self.assertEqual(
                {t["name"] for t in resp_tags}, {self.tag1.name, self.tag2.name}
            )

            new_contact_id = response.json()["id"]
            contact = self.env["res.partner"].browse(new_contact_id)
            self.assertTrue(contact.exists())
            self.assertEqual(contact.lastname, "doe")
            self.assertEqual(contact.firstname, "john")
            self.assertFalse(contact.is_company)
            self.assertEqual(contact.phone, "+34 911 111 111")
            self.assertEqual(contact.mobile, "+34 622 222 222")
            # DB persistence for relational fields
            self.assertEqual(contact.nationality_id.id, self.nationality.id)
            self.assertEqual(contact.state_id.id, self.state.id)
            self.assertEqual(contact.country_id.id, self.country.id)
            self.assertEqual(contact.property_payment_term_id.id, self.payment_term.id)
            self.assertEqual(contact.property_product_pricelist.id, self.pricelist.id)
            self.assertEqual(
                set(contact.category_id.ids), set([self.tag1.id, self.tag2.id])
            )

            detail = test_client.get(f"/contacts/{new_contact_id}")
            self.assertEqual(detail.status_code, status.HTTP_200_OK, detail.text)
            self.assertEqual(detail.json()["id"], new_contact_id)
            self.assertEqual(detail.json()["lastname"], "doe")
            self.assertEqual(detail.json()["name"], "john")
            self.assertIn("phones", detail.json())
            self.assertEqual(len(detail.json()["phones"]), 2)
            self.assertEqual(detail.json().get("contactType"), "person")
            # Detail payload for relational fields (nested objects)
            self.assertEqual(
                detail.json().get("nationality"),
                {"id": self.nationality.id, "name": self.nationality.name},
            )
            self.assertEqual(
                detail.json().get("state"),
                {"id": self.state.id, "name": self.state.name},
            )
            self.assertEqual(
                detail.json().get("country"),
                {"id": self.country.id, "name": self.country.name},
            )
            self.assertEqual(
                detail.json().get("paymentTerm"),
                {"id": self.payment_term.id, "name": self.payment_term.name},
            )
            self.assertEqual(
                detail.json().get("pricelist"),
                {"id": self.pricelist.id, "name": self.pricelist.name},
            )
            det_tags = detail.json().get("tags", [])
            self.assertEqual({t["id"] for t in det_tags}, {self.tag1.id, self.tag2.id})
            self.assertEqual(
                {t["name"] for t in det_tags}, {self.tag1.name, self.tag2.name}
            )

    def test_contact_patch(self):
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.patch(
                f"/contacts/{self.test_partner.id}",
                json={
                    "lastname": "doe_updated",
                    "name": "john_updated",
                    "contactType": "person",
                    "phones": [
                        {"type": "phone", "number": "+1 202 555 0100"},
                        {"type": "mobile", "number": "+1 202 555 0199"},
                    ],
                    "nationality": self.nationality2.id,
                    "state": self.state2.id,
                    "country": self.country2.id,
                    "paymentTerm": self.payment_term2.id,
                    "pricelist": self.pricelist2.id,
                    "tags": [self.tag_patch.id],
                },
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            self.assertIn("id", response.json())
            self.assertEqual(response.json()["id"], self.test_partner.id)
            self.assertEqual(response.json()["name"], "john_updated")
            self.assertEqual(response.json().get("lastname"), "doe_updated")
            self.assertEqual(response.json().get("contactType"), "person")
            self.assertIn("phones", response.json())
            self.assertEqual(len(response.json()["phones"]), 2)
            # Relational fields returned as nested objects after PATCH
            self.assertEqual(
                response.json().get("nationality"),
                {"id": self.nationality2.id, "name": self.nationality2.name},
            )
            self.assertEqual(
                response.json().get("state"),
                {"id": self.state2.id, "name": self.state2.name},
            )
            self.assertEqual(
                response.json().get("country"),
                {"id": self.country2.id, "name": self.country2.name},
            )
            self.assertEqual(
                response.json().get("paymentTerm"),
                {"id": self.payment_term2.id, "name": self.payment_term2.name},
            )
            self.assertEqual(
                response.json().get("pricelist"),
                {"id": self.pricelist2.id, "name": self.pricelist2.name},
            )
            resp_tags = response.json().get("tags", [])
            self.assertEqual({t["id"] for t in resp_tags}, {self.tag_patch.id})
            self.assertEqual({t["name"] for t in resp_tags}, {self.tag_patch.name})

            # Refresh environment and re-browse to ensure values are up to date
            self.env.invalidate_all()
            partner = self.env["res.partner"].browse(self.test_partner.id)
            self.assertEqual(partner.firstname, "john_updated")
            self.assertEqual(partner.lastname, "doe_updated")
            self.assertEqual(partner.company_type, "person")
            self.assertFalse(partner.is_agency)
            self.assertEqual(partner.phone, "+1 202 555 0100")
            self.assertEqual(partner.mobile, "+1 202 555 0199")
            # DB persistence for relational fields after PATCH
            self.assertEqual(partner.nationality_id.id, self.nationality2.id)
            self.assertEqual(partner.state_id.id, self.state2.id)
            self.assertEqual(partner.country_id.id, self.country2.id)
            self.assertEqual(partner.property_payment_term_id.id, self.payment_term2.id)
            self.assertEqual(partner.property_product_pricelist.id, self.pricelist2.id)
            self.assertEqual(set(partner.category_id.ids), set([self.tag_patch.id]))

    def test_contact_post_company_keeps_the_whole_name(self):
        """A multi-word company name is stored whole, not split as a person."""
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.post(
                "/contacts",
                json={"name": "Grand Hotel Centro SL", "contactType": "company"},
            )
            self.assertEqual(
                response.status_code, status.HTTP_201_CREATED, response.text
            )
            self.assertEqual(response.json()["name"], "Grand Hotel Centro SL")
            self.assertEqual(response.json()["lastname"], "")
            contact = self.env["res.partner"].browse(response.json()["id"])
            self.assertTrue(contact.is_company)
            self.assertEqual(contact.name, "Grand Hotel Centro SL")
            self.assertFalse(contact.firstname)

    def test_contact_post_rejects_removed_firstname_field(self):
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.post(
                "/contacts",
                json={
                    "name": "john",
                    "firstname": "john",
                    "contactType": "person",
                },
            )
            self.assertEqual(
                response.status_code,
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                response.text,
            )

    def test_contact_post_company_with_lastname(self):
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.post(
                "/contacts",
                json={
                    "name": "Grand Hotel Centro SL",
                    "lastname": "doe",
                    "contactType": "company",
                },
            )
            self.assertEqual(
                response.status_code,
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                response.text,
            )
            self.assertEqual(
                response.headers["content-type"], "application/problem+json"
            )
            self.assertEqual(
                response.json()["type"], "/errors/contact-lastname-not-applicable"
            )
            self.assertEqual(response.json()["field"], "lastname")

    def test_contact_post_company_with_empty_lastname(self):
        """An empty last name on a company is ignored, not rejected."""
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.post(
                "/contacts",
                json={
                    "name": "Grand Hotel Centro SL",
                    "lastname": "",
                    "contactType": "company",
                },
            )
            self.assertEqual(
                response.status_code, status.HTTP_201_CREATED, response.text
            )
            contact = self.env["res.partner"].browse(response.json()["id"])
            self.assertEqual(contact.name, "Grand Hotel Centro SL")

    def test_contact_post_without_name(self):
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.post("/contacts", json={"contactType": "person"})
            self.assertEqual(
                response.status_code,
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                response.text,
            )
            self.assertEqual(response.json()["type"], "/errors/contact-name-required")

    def test_contact_post_company_without_name(self):
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.post("/contacts", json={"contactType": "company"})
            self.assertEqual(
                response.status_code,
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                response.text,
            )
            self.assertEqual(response.json()["type"], "/errors/contact-name-required")

    def test_contact_patch_name_only_keeps_the_lastname(self):
        partner = self.env["res.partner"].create(
            {"firstname": "john", "lastname": "doe"}
        )
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.patch(
                f"/contacts/{partner.id}", json={"name": "johnny"}
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            self.assertEqual(response.json()["name"], "johnny")
            self.assertEqual(response.json()["lastname"], "doe")
        self.env.invalidate_all()
        self.assertEqual(partner.firstname, "johnny")
        self.assertEqual(partner.lastname, "doe")

    def test_contact_patch_lastname_only_keeps_the_name(self):
        partner = self.env["res.partner"].create(
            {"firstname": "john", "lastname": "doe"}
        )
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.patch(
                f"/contacts/{partner.id}", json={"lastname": "roe"}
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            self.assertEqual(response.json()["name"], "john")
            self.assertEqual(response.json()["lastname"], "roe")
        self.env.invalidate_all()
        self.assertEqual(partner.firstname, "john")
        self.assertEqual(partner.lastname, "roe")

    def test_contact_patch_company_other_field_keeps_the_name(self):
        partner = self.env["res.partner"].create(
            {"name": "Grand Hotel Centro SL", "is_company": True}
        )
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.patch(
                f"/contacts/{partner.id}", json={"email": "hotel@example.org"}
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            self.assertEqual(response.json()["name"], "Grand Hotel Centro SL")
        self.env.invalidate_all()
        self.assertEqual(partner.name, "Grand Hotel Centro SL")
        self.assertEqual(partner.email, "hotel@example.org")

    def test_contact_patch_company_with_lastname(self):
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.patch(
                f"/contacts/{self.test_company_partner.id}",
                json={"lastname": "doe"},
            )
            self.assertEqual(
                response.status_code,
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                response.text,
            )
            self.assertEqual(
                response.json()["type"], "/errors/contact-lastname-not-applicable"
            )

    def test_contact_patch_person_to_company_with_name(self):
        partner = self.env["res.partner"].create(
            {"firstname": "john", "lastname": "doe"}
        )
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.patch(
                f"/contacts/{partner.id}",
                json={"name": "Grand Hotel Centro SL", "contactType": "company"},
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            self.assertEqual(response.json()["contactType"], "company")
            self.assertEqual(response.json()["name"], "Grand Hotel Centro SL")
            self.assertEqual(response.json()["lastname"], "")
        self.env.invalidate_all()
        self.assertTrue(partner.is_company)
        self.assertEqual(partner.name, "Grand Hotel Centro SL")
        self.assertFalse(partner.firstname)

    def test_contact_patch_person_to_company_keeps_the_shown_name(self):
        partner = self.env["res.partner"].create(
            {"firstname": "john", "lastname": "doe"}
        )
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.patch(
                f"/contacts/{partner.id}", json={"contactType": "company"}
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            self.assertEqual(response.json()["contactType"], "company")
            self.assertEqual(response.json()["name"], "john doe")
            self.assertEqual(response.json()["lastname"], "")
        self.env.invalidate_all()
        self.assertEqual(partner.name, "john doe")
        self.assertFalse(partner.firstname)

    def test_contact_patch_company_to_person_keeps_the_shown_name(self):
        partner = self.env["res.partner"].create(
            {"name": "Grand Hotel Centro SL", "is_company": True}
        )
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.patch(
                f"/contacts/{partner.id}", json={"contactType": "person"}
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            self.assertEqual(response.json()["contactType"], "person")
            self.assertEqual(response.json()["name"], "Grand Hotel Centro SL")
            self.assertEqual(response.json()["lastname"], "")
        self.env.invalidate_all()
        self.assertFalse(partner.is_company)
        self.assertEqual(partner.name, "Grand Hotel Centro SL")

    def test_contact_patch_company_to_person_with_names(self):
        partner = self.env["res.partner"].create(
            {"name": "Grand Hotel Centro SL", "is_company": True}
        )
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.patch(
                f"/contacts/{partner.id}",
                json={"name": "john", "lastname": "doe", "contactType": "person"},
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            self.assertEqual(response.json()["name"], "john")
            self.assertEqual(response.json()["lastname"], "doe")
        self.env.invalidate_all()
        self.assertEqual(partner.firstname, "john")
        self.assertEqual(partner.lastname, "doe")

    def test_contact_patch_blank_name(self):
        partner = self.env["res.partner"].create(
            {"name": "Grand Hotel", "is_company": True}
        )
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.patch(
                f"/contacts/{partner.id}", json={"name": "   "}
            )
            self.assertEqual(
                response.status_code,
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                response.text,
            )
            self.assertEqual(response.json()["type"], "/errors/contact-name-required")
        self.env.invalidate_all()
        self.assertEqual(partner.name, "Grand Hotel")

    def test_contact_patch_person_without_any_name(self):
        partner = self.env["res.partner"].create({"firstname": "john"})
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.patch(f"/contacts/{partner.id}", json={"name": ""})
            self.assertEqual(
                response.status_code,
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                response.text,
            )
            self.assertEqual(response.json()["type"], "/errors/contact-name-required")
