from fastapi import status

from odoo.addons.pms_fastapi.tests.common import CommonTestPmsApi


class TestFoliosEndpoints(CommonTestPmsApi):
    def test_folios_get(self):
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.get("/folios")
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            self.assertIn("count", response.json())
            self.assertIn("items", response.json())
