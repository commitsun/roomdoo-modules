import os

from fastapi import status

from odoo.addons.pms_fastapi.tests.common import CommonTestPmsApi

from ..schemas.user import User

dir_path = os.path.dirname(os.path.realpath(__file__))


class TestUserEndpoints(CommonTestPmsApi):
    def test_user_get(self):
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.get("/user")
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            User(**response.json())

    def test_user_upload_image_and_delete(self):
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            # Upload a valid image
            with open(os.path.join(dir_path, "test_image.png"), "rb") as image_file:
                response = test_client.put(
                    "/user/image",
                    files={"image": ("test_image.png", image_file, "image/png")},
                )
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            User(**response.json())
            # Try to upload empty image. Should return 400 error.
            response = test_client.put(
                "/user/image",
                files={"image": ("empty_image.png", None, "image/png")},
            )
            self.assertEqual(
                response.status_code, status.HTTP_400_BAD_REQUEST, response.text
            )

            response = test_client.delete("/user/image")
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            self.assertEqual(response.json().get("image"), None)
            User(**response.json())
