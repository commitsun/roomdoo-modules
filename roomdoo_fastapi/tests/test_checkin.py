from fastapi import status

from odoo.addons.pms_fastapi.tests.common import CommonTestCheckin


class TestCheckinContextBrand(CommonTestCheckin):
    def _context(self):
        with self._create_test_client() as test_client:
            self._login(test_client)
            return test_client.get(f"/folios/{self.folio.id}/checkin-context")

    def test_brand_name_is_the_instance_name(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "roomdoo_fastapi.instance_name", "Acme PMS"
        )
        response = self._context()
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        self.assertEqual(response.json()["brandName"], "Acme PMS")

    def test_brand_name_defaults_to_roomdoo(self):
        self.env["ir.config_parameter"].sudo().search(
            [("key", "=", "roomdoo_fastapi.instance_name")]
        ).unlink()
        response = self._context()
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        self.assertEqual(response.json()["brandName"], "Roomdoo")
