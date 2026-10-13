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
                "lastname2": "any",
            }
        )
        cls.test_company_partner = cls.env["res.partner"].create(
            {
                "name": "Grand Hotel Group SL",
                "is_company": True,
            }
        )

    def test_contact_detail_get(self):
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.get(f"/contacts/{self.test_partner.id}")
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            self.assertEqual(response.json()["name"], "john")
            self.assertEqual(response.json()["lastname"], "doe")
            self.assertEqual(response.json()["lastname2"], "any")

    def test_contact_detail_get_company(self):
        """A company never shows a second last name either."""
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.get(f"/contacts/{self.test_company_partner.id}")
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            self.assertEqual(response.json()["name"], "Grand Hotel Group SL")
            self.assertEqual(response.json()["lastname"], "")
            self.assertEqual(response.json()["lastname2"], "")

    def test_contact_post_person(self):
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.post(
                "/contacts",
                json={
                    "name": "jane",
                    "lastname": "doe",
                    "lastname2": "roe",
                    "contactType": "person",
                },
            )
            self.assertEqual(
                response.status_code, status.HTTP_201_CREATED, response.text
            )
            self.assertEqual(response.json()["name"], "jane")
            self.assertEqual(response.json()["lastname"], "doe")
            self.assertEqual(response.json()["lastname2"], "roe")
            contact = self.env["res.partner"].browse(response.json()["id"])
            self.assertEqual(contact.firstname, "jane")
            self.assertEqual(contact.lastname, "doe")
            self.assertEqual(contact.lastname2, "roe")

    def test_contact_post_company_with_lastname2(self):
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.post(
                "/contacts",
                json={
                    "name": "Grand Hotel Centro SL",
                    "lastname2": "roe",
                    "contactType": "company",
                },
            )
            self.assertEqual(
                response.status_code,
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                response.text,
            )
            self.assertEqual(
                response.json()["type"], "/errors/contact-lastname-not-applicable"
            )
            self.assertEqual(response.json()["field"], "lastname2")

    def test_contact_patch_person_to_company_clears_the_last_names(self):
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.patch(
                f"/contacts/{self.test_partner.id}",
                json={"contactType": "company"},
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            self.assertEqual(response.json()["name"], "john doe any")
            self.assertEqual(response.json()["lastname"], "")
            self.assertEqual(response.json()["lastname2"], "")
        self.env.invalidate_all()
        self.assertEqual(self.test_partner.name, "john doe any")
        self.assertFalse(self.test_partner.firstname)
        self.assertFalse(self.test_partner.lastname2)
