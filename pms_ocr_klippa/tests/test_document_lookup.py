# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo.tests import TransactionCase, tagged

DOCUMENT_NUMBER = "LOOKUP123"


@tagged("post_install", "-at_install")
class TestDocumentLookup(TransactionCase):
    """The data of a partner with the same document number completes the OCR."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pms_property = cls.env["pms.property"]
        category = cls.env["res.partner.id_category"].create(
            {"name": "Lookup document", "code": "LKP"}
        )
        partner = cls.env["res.partner"].create(
            {"firstname": "Ann", "lastname": "Lee", "birthdate_date": "1990-05-17"}
        )
        cls.env["res.partner.id_number"].create(
            {
                "name": DOCUMENT_NUMBER,
                "category_id": category.id,
                "partner_id": partner.id,
            }
        )
        cls.document_data = {"document_number": {"value": DOCUMENT_NUMBER}}

    def test_the_partner_with_the_document_completes_the_result(self):
        data = self.pms_property._map_klippa_data(self.document_data)
        self.assertEqual(data["firstname"], "Ann")
        self.assertEqual(data["lastname"], "Lee")

    def test_the_lookup_can_be_skipped(self):
        data = self.pms_property.with_context(
            ocr_skip_document_lookup=True
        )._map_klippa_data(self.document_data)
        self.assertEqual(data["document_number"], DOCUMENT_NUMBER)
        self.assertNotIn("firstname", data)
        self.assertNotIn("lastname", data)
