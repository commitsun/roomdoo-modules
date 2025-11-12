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

    def test_unique_aeat_identification(self):
        """
        Check the constraints with a partner with a res.partner.id_number
        and another with the same AEAT identification type and number.
        """
        identification_type_passport = self.env["res.partner.id_category"].search(
            [("partner_map_field", "=", "passport")], limit=1
        )
        self.env["res.partner.id_number"].create(
            {
                "partner_id": self.partner_1.id,
                "category_id": identification_type_passport.id,
                "name": "A12345678",
                "country_id": self.env.ref("base.es").id,
            }
        )
        with self.assertRaises(ValidationError):
            self.env["res.partner.id_number"].create(
                {
                    "partner_id": self.partner_2.id,
                    "category_id": identification_type_passport.id,
                    "name": "A12345678",
                    "country_id": self.env.ref("base.es").id,
                }
            )

    def test_unique_aeat_fields(self):
        """
        Check the constraints with a partner with AEAT identification
        and another with the same AEAT identification type and number.
        """
        self.partner_1.write(
            {
                "aeat_identification": "A12345678",
                "aeat_identification_type": "03",
            }
        )
        with self.assertRaises(ValidationError):
            self.partner_2.write(
                {
                    "aeat_identification": "A12345678",
                    "aeat_identification_type": "03",
                }
            )

    def test_unique_aeat_identification_aeat_fields(self):
        """
        Check the constraints with a partner with AEAT identification
        and another with a res.partner.id_number with the same AEAT
        identification type and number.
        """
        identification_type_passport = self.env["res.partner.id_category"].search(
            [("partner_map_field", "=", "passport")], limit=1
        )
        self.partner_1.write(
            {
                "aeat_identification": "A12345678",
                "aeat_identification_type": "03",
            }
        )
        with self.assertRaises(ValidationError):
            self.env["res.partner.id_number"].create(
                {
                    "partner_id": self.partner_2.id,
                    "category_id": identification_type_passport.id,
                    "name": "A12345678",
                    "country_id": self.env.ref("base.es").id,
                }
            )
