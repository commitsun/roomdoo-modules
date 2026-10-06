from fastapi import status

from odoo.addons.pms_fastapi.tests.common import CommonTestPmsApi


class TestInvoiceContactValidationL10nEs(CommonTestPmsApi):
    """GET /invoices/validate-contact — Spanish invoicing requirements.

    A contact can invoice if it has a VAT *or* both AEAT identification type
    and number. On top of that, a Spanish-issued passport (AEAT type 03) cannot
    be used to invoice.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.country_es = cls.env.ref("base.es")
        cls.country_fr = cls.env.ref("base.fr")
        cls.passport_category = cls.env.ref(
            "pms_partner_identification.document_type_passport"
        )

    def _validate_url(self, contact_id, property_id=None):
        property_id = self.test_property.id if property_id is None else property_id
        return (
            "/invoices/validate-contact"
            f"?pmsPropertyId={property_id}&contactId={contact_id}"
        )

    def _validate(self, partner):
        with self._create_test_client() as test_client:
            self._login(test_client)
            return test_client.get(self._validate_url(partner.id))

    def _error_types(self, response):
        return [e["type"] for e in response.json()["errors"]]

    def _add_passport_id_number(self, partner, country, name):
        return self.env["res.partner.id_number"].create(
            {
                "name": name,
                "category_id": self.passport_category.id,
                "country_id": country.id,
                "partner_id": partner.id,
            }
        )

    def test_vat_only_is_valid(self):
        partner = self.env["res.partner"].create(
            {"firstname": "John", "lastname": "Doe", "vat": "ES12345678Z"}
        )
        response = self._validate(partner)
        self.assertEqual(
            response.status_code, status.HTTP_204_NO_CONTENT, response.text
        )

    def test_aeat_identification_non_passport_is_valid(self):
        partner = self.env["res.partner"].create(
            {
                "firstname": "Res",
                "lastname": "Cert",
                "aeat_identification_type": "05",
                "aeat_identification": "X1234567",
            }
        )
        response = self._validate(partner)
        self.assertEqual(
            response.status_code, status.HTTP_204_NO_CONTENT, response.text
        )

    def test_incomplete_aeat_identification_is_invalid(self):
        # AEAT type set but no identification number, and no VAT.
        partner = self.env["res.partner"].create(
            {
                "firstname": "Half",
                "lastname": "Aeat",
                "aeat_identification_type": "03",
            }
        )
        response = self._validate(partner)
        self.assertEqual(
            response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY, response.text
        )
        self.assertEqual(self._error_types(response), ["/errors/missing-fiscal-id"])

    def test_spanish_passport_is_invalid(self):
        # AEAT passport type with a number (fiscal id present) plus a passport
        # document issued by Spain, which is what must be rejected.
        partner = self.env["res.partner"].create(
            {
                "firstname": "Pass",
                "lastname": "Es",
                "aeat_identification_type": "03",
                "aeat_identification": "ESID001",
            }
        )
        self._add_passport_id_number(partner, self.country_es, "ES-PASS-001")
        response = self._validate(partner)
        self.assertEqual(
            response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY, response.text
        )
        self.assertEqual(
            self._error_types(response), ["/errors/invalid-id-type-for-country"]
        )

    def test_non_spanish_passport_is_valid(self):
        partner = self.env["res.partner"].create(
            {
                "firstname": "Pass",
                "lastname": "Fr",
                "aeat_identification_type": "03",
                "aeat_identification": "FRID001",
            }
        )
        self._add_passport_id_number(partner, self.country_fr, "FR-PASS-001")
        response = self._validate(partner)
        self.assertEqual(
            response.status_code, status.HTTP_204_NO_CONTENT, response.text
        )

    def test_spanish_passport_without_fiscal_id_accumulates_errors(self):
        # AEAT passport type set but no identification number (fiscal id
        # missing) while holding a Spanish passport: both errors must report.
        partner = self.env["res.partner"].create(
            {
                "firstname": "Pass",
                "lastname": "NoId",
                "aeat_identification_type": "03",
            }
        )
        self._add_passport_id_number(partner, self.country_es, "ES-PASS-002")
        response = self._validate(partner)
        self.assertEqual(
            response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY, response.text
        )
        self.assertEqual(
            set(self._error_types(response)),
            {"/errors/missing-fiscal-id", "/errors/invalid-id-type-for-country"},
        )
