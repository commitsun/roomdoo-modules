from fastapi import status

from odoo.addons.pms_fastapi.tests.common import CommonTestRoomdooApi

from ..schemas.pms_property import PropertySummary


class TestPropertiesEndpoints(CommonTestRoomdooApi):
    def test_properties_get(self):
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.get("/pmsProperties")
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            PropertySummary(**response.json()[0])
