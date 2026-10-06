from fastapi import status

from odoo.addons.pms_fastapi.tests.common import CommonTestPmsApi


class TestInvoiceContactValidation(CommonTestPmsApi):
    """GET /invoices/validate-contact — base invoicing requirements.

    The base implementation only requires a VAT number. The Spanish-specific
    rules (AEAT identification, passport restrictions) are tested in
    pms_fastapi_l10n_es.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner_with_vat = cls.env["res.partner"].create(
            {
                "firstname": "John",
                "lastname": "Doe",
                "vat": "ES12345678Z",
            }
        )
        cls.partner_without_fiscal_id = cls.env["res.partner"].create(
            {
                "firstname": "Jane",
                "lastname": "Roe",
            }
        )

    def _validate_url(self, contact_id, property_id=None):
        property_id = self.test_property.id if property_id is None else property_id
        return (
            "/invoices/validate-contact"
            f"?pmsPropertyId={property_id}&contactId={contact_id}"
        )

    def test_contact_with_vat_is_valid(self):
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get(self._validate_url(self.partner_with_vat.id))
        self.assertEqual(
            response.status_code, status.HTTP_204_NO_CONTENT, response.text
        )

    def test_contact_without_fiscal_id_is_invalid(self):
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get(
                self._validate_url(self.partner_without_fiscal_id.id)
            )
        self.assertEqual(
            response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY, response.text
        )
        body = response.json()
        self.assertEqual(body["type"], "/errors/invoicing-validation-failed")
        error_types = [e["type"] for e in body["errors"]]
        self.assertIn("/errors/missing-fiscal-id", error_types)

    def test_unknown_contact_returns_404(self):
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get(self._validate_url(999999999))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.text)

    def test_unknown_property_returns_404(self):
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get(
                self._validate_url(self.partner_with_vat.id, property_id=999999999)
            )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.text)
