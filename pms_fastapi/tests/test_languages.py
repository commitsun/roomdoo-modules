from fastapi import status

from odoo.addons.pms_fastapi.tests.common import CommonTestPmsApi

from ..schemas.language import Language


class TestLanguagesEndpoints(CommonTestPmsApi):
    def test_languages_get(self):
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.get("/languages")
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            Language(**response.json()[0])
