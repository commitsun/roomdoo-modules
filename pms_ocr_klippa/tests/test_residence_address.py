# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestResidenceAddress(TransactionCase):
    """The OCR maps the residence address read from the document."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pms_property = cls.env["pms.property"]
        cls.spain = cls.env.ref("base.es")
        cls.argentina = cls.env.ref("base.ar")

    def test_state_by_name(self):
        get_state = self.pms_property._get_state
        # Scores the same against León
        self.assertEqual(
            get_state("VALENCIA", self.spain.id), self.env.ref("base.state_es_v")
        )
        self.assertEqual(
            get_state("CASTELLON/CASTELLO", self.spain.id),
            self.env.ref("base.state_es_cs"),
        )

    def test_state_name_of_several_countries(self):
        # There is a Córdoba state in Spain, Argentina and Colombia
        get_state = self.pms_property._get_state
        self.assertEqual(
            get_state("CÓRDOBA", self.argentina.id), self.env.ref("base.state_ar_x")
        )
        self.assertFalse(get_state("CÓRDOBA", False))

    def test_country_of_the_document(self):
        # Spanish ID cards print no country in the address
        data = self.pms_property._complete_residence_address(
            {
                "street_name": "CALLE MAYOR",
                "house_number": "1",
                "city": "MISLATA",
                "province": "VALENCIA",
                "postcode": "46920",
            },
            {},
            document_country_id=self.spain.id,
        )
        self.assertEqual(data["country_id"], self.spain.id)
        self.assertEqual(data["country_state"], self.env.ref("base.state_es_v").id)
