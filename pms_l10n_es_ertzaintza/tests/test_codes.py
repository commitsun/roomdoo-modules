# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

from odoo.tests import BaseCase

from ..models.ertzaintza_codes import classify_error_codes, extract_error_code


class TestErtzaintzaCodes(BaseCase):
    def test_extract_error_code(self):
        self.assertEqual(extract_error_code("C_1|1_PER43_TI"), "PER43")
        self.assertEqual(extract_error_code("C_1_CTO01"), "CTO01")
        self.assertEqual(extract_error_code("PET07"), "PET07")
        self.assertEqual(extract_error_code("403"), "403")

    def test_classify_error_codes(self):
        self.assertEqual(classify_error_codes(["CTO01"]), "duplicate")
        self.assertEqual(classify_error_codes(["CTO01", "PER43"]), "data")
        self.assertEqual(classify_error_codes(["403"]), "auth")
        self.assertEqual(classify_error_codes(["999"]), "transient")
        self.assertEqual(classify_error_codes(["PER43"]), "data")
