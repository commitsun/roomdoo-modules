from odoo.tests import common


class TestPartnerIdentificationMap(common.TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner_1 = cls.env["res.partner"].create(
            {
                "name": "Partner 1",
            }
        )
        cls.identification_type_vat = cls.env["res.partner.id_category"].create(
            {
                "name": "VAT Type",
                "partner_map_field": "vat",
                "code": "vat",
            }
        )

    def test_map_field_vat(self):
        document_vat = self.env["res.partner.id_number"].create(
            {
                "partner_id": self.partner_1.id,
                "category_id": self.identification_type_vat.id,
                "name": "12345678Z",
                "country_id": self.env.ref("base.es").id,
            }
        )
        document_vat.set_partner_id_field()
        # Check that the partner's VAT has been set correctly, with country code.
        self.assertEqual(
            self.partner_1.vat,
            "ES12345678Z",
            "The partner's VAT should be set with country code prefix.",
        )

    def test_map_field_vat_no_country_prefix(self):
        self.partner_1.country_id = self.env.ref("base.es")
        document_vat = self.env["res.partner.id_number"].create(
            {
                "partner_id": self.partner_1.id,
                "category_id": self.identification_type_vat.id,
                "name": "12345678Z",
                "country_id": self.env.ref("base.es").id,
            }
        )
        document_vat.set_partner_id_field()
        # Check that the partner's VAT has been set correctly, without country code.
        self.assertEqual(
            self.partner_1.vat,
            "12345678Z",
            "The partner's VAT should be set without country code prefix.",
        )
