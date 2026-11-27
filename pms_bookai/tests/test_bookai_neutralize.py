from odoo.modules.neutralize import get_neutralization_queries
from odoo.tests import TransactionCase, tagged

BOOKAI_PARAMS = (
    "pms_bookai.api_endpoint",
    "pms_bookai.api_token",
    "pms_bookai.user_token_private_key",
)


@tagged("post_install", "-at_install")
class TestBookaiNeutralize(TransactionCase):
    def test_neutralizing_a_copy_drops_the_bookai_connection(self):
        icp = self.env["ir.config_parameter"].sudo()
        for key in BOOKAI_PARAMS:
            icp.set_param(key, "production-value")
        icp.set_param("pms_bookai.other_setting", "10")

        for query in get_neutralization_queries(["pms_bookai"]):
            self.env.cr.execute(query)
        self.env["ir.config_parameter"].invalidate_model()

        for key in BOOKAI_PARAMS:
            self.assertFalse(icp.get_param(key), key)
        # Other settings of the module stay as they were.
        self.assertEqual(icp.get_param("pms_bookai.other_setting"), "10")
