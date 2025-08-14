from fastapi import status

from odoo.addons.pms_fastapi.tests.common import CommonTestRoomdooApi

from ..schemas.instance import Instance


class TestInstanceEndpoints(CommonTestRoomdooApi):
    def test_instance_get(self):
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.get("/instance")
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            Instance(**response.json())
