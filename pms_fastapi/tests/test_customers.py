from fastapi import status

from odoo.addons.pms_fastapi.tests.common import CommonTestRoomdooApi


class TestCustomersEndpoints(CommonTestRoomdooApi):
    def test_customers_get(self):
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.get("/customers")
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            self.assertIn("count", response.json())
            self.assertIn("items", response.json())
