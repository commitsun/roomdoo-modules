from fastapi import status

from odoo.addons.pms_fastapi.tests.common import CommonTestRoomdooApi


class TestContactsEndpoints(CommonTestRoomdooApi):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.test_partner = cls.env["res.partner"].create(
            {
                "firstname": "john",
                "lastname": "doe",
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
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.get(f"/contacts/{self.test_partner.id}")
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)

    def test_contact_post(self):
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.post(
                "/contacts",
                json={
                    "lastname": "doe",
                    "firstname": "john",
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
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            self.assertIn("id", response.json())
            self.assertEqual(response.json()["lastname"], "doe")
            self.assertEqual(response.json()["firstname"], "john")
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
            self.assertEqual(detail.json()["firstname"], "john")
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
                    "firstname": "john_updated",
                    "contactType": "company",
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
            self.assertEqual(response.json()["firstname"], "john_updated")
            self.assertEqual(response.json().get("lastname"), "doe_updated")
            self.assertEqual(response.json().get("contactType"), "company")
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
            self.assertEqual(partner.company_type, "company")
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
