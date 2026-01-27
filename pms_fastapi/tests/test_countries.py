from fastapi import status

from odoo.addons.pms_fastapi.tests.common import CommonTestPmsApi

from ..schemas.country import CountrySummary


class TestCountriesEndpoints(CommonTestPmsApi):
    def test_countries_get(self):
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.get("/countries")
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            CountrySummary(**response.json()[0])
