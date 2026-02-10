from fastapi import status

from odoo.addons.pms_fastapi.tests.common import CommonTestPmsApi


class TestJournalsEndpoints(CommonTestPmsApi):
    def test_journals_get(self):
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.get("/journals")
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            self.assertIsInstance(response.json(), list)
