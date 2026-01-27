import base64
import os

from fastapi import status

from odoo.addons.pms_fastapi.tests.common import CommonTestPmsApi

from ..schemas.instance import Instance

dir_path = os.path.dirname(os.path.realpath(__file__))


class TestInstanceEndpoints(CommonTestPmsApi):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        with open(os.path.join(dir_path, "roomdoo.jpg"), "rb") as f:
            image_bytes = f.read()
            image_base64 = base64.b64encode(image_bytes).decode("utf-8")
            cls.config = (
                cls.env["res.config.settings"]
                .create(
                    {
                        "roomdoo_fastapi_instance_name": "lorem ipsum",
                        "roomdoo_fastapi_image": image_base64,
                    }
                )
                .execute()
            )

    def test_instance_get(self):
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.get("/instance")
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            Instance(**response.json())
