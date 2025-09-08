from fastapi import status

from odoo.addons.pms_fastapi.tests.common import CommonTestRoomdooApi


class TestLinksEndpoints(CommonTestRoomdooApi):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.test_link = cls.env["roomdoo.app.menu"].create(
            {"name": "test_url", "base_url": "https://anyurl.com/"}
        )

    def test_get_links(self):
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.get(f"/pms-properties/{self.test_property.id}/links")
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)

    def test_render_link(self):
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.get(
                f"/pms-properties/{self.test_property.id}/links/{self.test_link.id}"
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            self.assertEqual(response.json(), self.test_link.base_url)
