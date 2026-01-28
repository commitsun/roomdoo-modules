from odoo.exceptions import ValidationError
from odoo.tests import common


class TestPms(common.TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner_1 = cls.env["res.partner"].create(
            {
                "name": "Partner 1",
            }
        )
        cls.partner_2 = cls.env["res.partner"].create(
            {
                "name": "Partner 2",
            }
        )

    def test_unique_vat_country_code(self):
        """
        Check the constraints with a partner with country code in VAT and another
        partner trying to use the same VAT without country code but with same country.
        """
        self.partner_1.with_context(test_vat=True).write({"vat": "ES12345678Z"})
        self.partner_2.write({"country_id": self.env.ref("base.es").id})
        with self.assertRaises(ValidationError):
            self.partner_2.with_context(test_vat=True).write({"vat": "12345678Z"})

    def test_unique_vat_identification_vat_type(self):
        """
        Check the constraints with a partner with a country code in VAT and
        another with a res.partner.id_number with vat type and the same number

        """
        self.partner_1.with_context(test_vat=True).write({"vat": "ES12345678Z"})
        identification_type_vat = self.env["res.partner.id_category"].create(
            {
                "name": "test_vat_type",
                "code": "VAT",
                "partner_map_field": "vat",
            }
        )
        with self.assertRaises(ValidationError):
            self.env["res.partner.id_number"].create(
                {
                    "partner_id": self.partner_2.id,
                    "category_id": identification_type_vat.id,
                    "name": "12345678Z",
                    "country_id": self.env.ref("base.es").id,
                }
            )
