from fastapi import status

from odoo.addons.pms_fastapi.tests.common import CommonTestPmsApi


class TestContactsEndpoints(CommonTestPmsApi):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.test_partner_vat = cls.env["res.partner"].create(
            {
                "firstname": "john",
                "lastname": "doe",
                "lastname2": "any",
                "vat": "ES12345678Z",
            }
        )
        cls.test_partner_aeat = cls.env["res.partner"].create(
            {
                "firstname": "john",
                "lastname": "doe",
                "lastname2": "any",
                "aeat_identification_type": "03",
                "aeat_identification": "ABC123456",
            }
        )

    def test_get_contact_fiscal_data(self):
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.get(f"/contacts/{self.test_partner_vat.id}")
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            self.assertEqual(response.json().get("fiscalIdNumber"), "ES12345678Z")
            self.assertEqual(response.json().get("fiscalIdNumberType"), "vat")
            response = test_client.get(f"/contacts/{self.test_partner_aeat.id}")
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            self.assertEqual(response.json().get("fiscalIdNumber"), "ABC123456")
            self.assertEqual(response.json().get("fiscalIdNumberType"), "passport")

    def test_update_aeat_identification(self):
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.patch(
                f"/contacts/{self.test_partner_aeat.id}",
                json={"fiscalIdNumberType": "other"},
            )
            self.env.invalidate_all()
            self.assertEqual(response.json().get("fiscalIdNumberType"), "other")
            self.assertEqual(response.json().get("fiscalIdNumber"), "ABC123456")
            self.assertEqual(self.test_partner_aeat.aeat_identification_type, "06")
            self.assertEqual(self.test_partner_aeat.aeat_identification, "ABC123456")

            response = test_client.patch(
                f"/contacts/{self.test_partner_aeat.id}",
                json={"fiscalIdNumber": "XYZ987654"},
            )
            self.env.invalidate_all()
            self.assertEqual(response.json().get("fiscalIdNumberType"), "other")
            self.assertEqual(response.json().get("fiscalIdNumber"), "XYZ987654")
            self.assertEqual(self.test_partner_aeat.aeat_identification_type, "06")
            self.assertEqual(self.test_partner_aeat.aeat_identification, "XYZ987654")

            response = test_client.patch(
                f"/contacts/{self.test_partner_aeat.id}",
                json={"fiscalIdNumber": "ABC123456", "fiscalIdNumberType": "passport"},
            )
            self.env.invalidate_all()
            self.assertEqual(response.json().get("fiscalIdNumberType"), "passport")
            self.assertEqual(response.json().get("fiscalIdNumber"), "ABC123456")
            self.assertEqual(self.test_partner_aeat.aeat_identification_type, "03")
            self.assertEqual(self.test_partner_aeat.aeat_identification, "ABC123456")

    def test_update_vat_identification(self):
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.patch(
                f"/contacts/{self.test_partner_vat.id}",
                json={"fiscalIdNumber": "ES87654321X"},
            )
            self.env.invalidate_all()
            self.assertEqual(response.json().get("fiscalIdNumberType"), "vat")
            self.assertEqual(response.json().get("fiscalIdNumber"), "ES87654321X")
            self.assertEqual(self.test_partner_vat.vat, "ES87654321X")

    def test_change_vat_to_aeat_identification(self):
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.patch(
                f"/contacts/{self.test_partner_vat.id}",
                json={"fiscalIdNumber": "ABC123456", "fiscalIdNumberType": "passport"},
            )
            self.env.invalidate_all()
            self.assertEqual(response.json().get("fiscalIdNumberType"), "passport")
            self.assertEqual(response.json().get("fiscalIdNumber"), "ABC123456")
            self.assertEqual(self.test_partner_vat.vat, False)
            self.assertEqual(self.test_partner_vat.aeat_identification_type, "03")
            self.assertEqual(self.test_partner_vat.aeat_identification, "ABC123456")

    def test_change_aeat_identification_to_vat(self):
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.patch(
                f"/contacts/{self.test_partner_aeat.id}",
                json={"fiscalIdNumber": "ES87654321X", "fiscalIdNumberType": "vat"},
            )
            self.env.invalidate_all()
            self.assertEqual(response.json().get("fiscalIdNumberType"), "vat")
            self.assertEqual(response.json().get("fiscalIdNumber"), "ES87654321X")
            self.assertEqual(self.test_partner_aeat.vat, "ES87654321X")
            self.assertEqual(self.test_partner_aeat.aeat_identification_type, False)
            self.assertEqual(self.test_partner_aeat.aeat_identification, False)
