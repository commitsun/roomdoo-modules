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
                "lastname2": "any",
            }
        )

    def test_contact_detail_get(self):
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.get(f"/contacts/{self.test_partner.id}")
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            self.assertIn("lastname2", response.json())
