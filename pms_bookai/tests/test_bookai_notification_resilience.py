from odoo.tools import mute_logger

from odoo.addons.pms.tests.common import TestPms


class TestBookaiNotificationResilience(TestPms):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["pms.property.notification.rule"].search([("active", "=", True)]).write(
            {"active": False}
        )
        cls.folio_model = cls.env.ref("pms.model_pms_folio")
        cls.env["ir.config_parameter"].sudo().set_param("pms_bookai.api_token", "")

        cls.template = cls.env["pms.notification.template"].create(
            {
                "name": "BookAI resilience template",
                "code": "bookai_resilience_template",
                "model_id": cls.folio_model.id,
            }
        )
        cls.folio = cls.env["pms.folio"].create(
            {
                "pms_property_id": cls.pms_property1.id,
                "partner_name": "BookAI Resilience Guest",
            }
        )

    @mute_logger("odoo.addons.pms_bookai.models.pms_notification_log")
    def test_bookai_prepare_failure_does_not_block_log_create(self):
        log = self.env["pms.notification.log"].create(
            {
                "name": "BookAI resilience log",
                "template_id": self.template.id,
                "channel": "bookai_whatsapp",
                "origin_model": "pms.folio",
                "origin_res_id": self.folio.id,
                "recipient_mode": "template",
            }
        )

        self.assertTrue(log.exists())
        self.assertEqual(log.state, "error")
        self.assertTrue(log.error_message)
