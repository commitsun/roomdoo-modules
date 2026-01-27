from fastapi import status

from odoo.addons.pms_fastapi.tests.common import CommonTestPmsApi


class TestContactIdNumberEndpoints(CommonTestPmsApi):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        # Base partner
        cls.test_partner = cls.env["res.partner"].create(
            {
                "firstname": "John",
                "lastname": "Doe",
                "company_type": "person",
            }
        )

        # Countries
        cls.country_es = cls.env["res.country"].search(
            [("code", "=", "ES")], limit=1
        ) or cls.env["res.country"].create({"name": "Spain", "code": "ES"})
        cls.country_fr = cls.env["res.country"].search(
            [("code", "=", "FR")], limit=1
        ) or cls.env["res.country"].create({"name": "France", "code": "FR"})

        # ID number categories
        cls.category_es = cls.env["res.partner.id_category"].create(
            {
                "name": "Passport",
                "code": "PASS",
                "country_ids": [(6, 0, [cls.country_es.id])],
            }
        )
        cls.category_all = cls.env["res.partner.id_category"].create(
            {"name": "National ID", "code": "NID", "country_ids": [(6, 0, [])]}
        )

        # One existing id number for GET scenarios
        cls.id_number_1 = cls.env["res.partner.id_number"].create(
            {
                "name": "12345678Z",
                "support_number": "ABC-001",
                "category_id": cls.category_all.id,
                "country_id": cls.country_fr.id,
                "partner_id": cls.test_partner.id,
            }
        )

    def test_id_number_categories_get_all(self):
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get("/id-number-categories")
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            items = response.json()
            self.assertIsInstance(items, list)
            # Expect both categories
            ids = {c["id"] for c in items}
            self.assertIn(self.category_es.id, ids)
            self.assertIn(self.category_all.id, ids)

    def test_id_number_categories_filtered_by_country(self):
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get(
                f"/id-number-categories?country={self.country_es.id}"
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            items = response.json()
            ids = {c["id"] for c in items}
            # Should include categories with ES or with no country restriction
            self.assertIn(self.category_es.id, ids)
            self.assertIn(self.category_all.id, ids)

    def test_id_number_categories_filtered_by_country_fr(self):
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get(
                f"/id-number-categories?country={self.country_fr.id}"
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            items = response.json()
            ids = {c["id"] for c in items}
            # Should only include the category with
            # no country restriction (category_all)
            self.assertIn(self.category_all.id, ids)
            self.assertNotIn(self.category_es.id, ids)

    def test_contact_id_numbers_get_not_found(self):
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get("/contacts/9999999/id-numbers")
            self.assertEqual(
                response.status_code, status.HTTP_404_NOT_FOUND, response.text
            )
            self.assertEqual(response.json().get("detail"), "contact not found")

    def test_contact_id_numbers_get(self):
        # Ensure there is another id number to test list shape
        self.env["res.partner.id_number"].create(
            {
                "name": "XK-0001",
                "support_number": "SN-002",
                "category_id": self.category_es.id,
                "country_id": self.country_es.id,
                "partner_id": self.test_partner.id,
            }
        )
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get(f"/contacts/{self.test_partner.id}/id-numbers")
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            items = response.json()
            self.assertTrue(len(items) >= 2)
            any_with_category = any(i.get("category") for i in items)
            self.assertTrue(any_with_category)

    def test_contact_id_number_post(self):
        with self._create_test_client() as test_client:
            self._login(test_client)
            payload = {
                "name": "ES-XYZ-777",
                "category": self.category_es.id,
                "supportNumber": "DOC-777",
                "country": self.country_es.id,
            }
            response = test_client.post(
                f"/contacts/{self.test_partner.id}/id-numbers",
                json=payload,
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            data = response.json()
            self.assertIn("id", data)
            self.assertEqual(data.get("name"), "ES-XYZ-777")
            self.assertEqual(data.get("supportNumber"), "DOC-777")
            self.assertIsNotNone(data.get("category"))
            self.assertIsNotNone(data.get("country"))

            # DB side: record exists and belongs to partner
            rec = self.env["res.partner.id_number"].browse(data["id"])  # type: ignore
            self.assertTrue(rec.exists())
            self.assertEqual(rec.partner_id.id, self.test_partner.id)
            self.assertEqual(rec.category_id.id, self.category_es.id)
            self.assertEqual(rec.country_id.id, self.country_es.id)

    def test_contact_id_number_patch(self):
        # Create a record to update
        rec = self.id_number_1
        with self._create_test_client() as test_client:
            self._login(test_client)
            payload = {
                "name": "UPDATED-001",
                "category": self.category_es.id,
                "supportNumber": "SUP-999",
                "country": self.country_es.id,
            }
            response = test_client.patch(
                f"/contacts/{self.test_partner.id}/id-numbers/{rec.id}",
                json=payload,
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            data = response.json()
            self.assertEqual(data.get("name"), "UPDATED-001")
            self.assertEqual(data.get("supportNumber"), "SUP-999")

            self.env.invalidate_all()
            self.assertEqual(rec.partner_id.id, self.test_partner.id)
            self.assertEqual(rec.category_id.id, self.category_es.id)
            self.assertEqual(rec.country_id.id, self.country_es.id)

    def test_contact_id_number_patch_wrong_owner(self):
        other_partner = self.env["res.partner"].create(
            {"firstname": "Jane", "lastname": "Smith", "company_type": "person"}
        )
        other_idnum = self.env["res.partner.id_number"].create(
            {
                "name": "OTHER-001",
                "support_number": "SUP-002",
                "category_id": self.category_all.id,
                "country_id": self.country_fr.id,
                "partner_id": other_partner.id,
            }
        )
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.patch(
                f"/contacts/{self.test_partner.id}/id-numbers/{other_idnum.id}",
                json={"name": "IGNORED"},
            )
            self.assertEqual(
                response.status_code, status.HTTP_400_BAD_REQUEST, response.text
            )
            # Keep the exact detail from the router for robustness
            self.assertIn("does not belog", response.json().get("detail", ""))
