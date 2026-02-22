from odoo.exceptions import ValidationError

from odoo.addons.pms.tests.common import TestPms


class TestPmsPropertyNotificationRule(TestPms):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.folio_model = cls.env.ref("pms.model_pms_folio")
        cls.env["pms.property.notification.rule"].search(
            [
                ("target_model_name", "=", "pms.folio"),
                ("rule_type", "=", "event"),
                ("active", "=", True),
            ]
        ).write({"active": False})

        cls.mail_template = cls.env["mail.template"].create(
            {
                "name": "Rule pre-domain test template",
                "model_id": cls.folio_model.id,
                "subject": "Test",
                "body_html": "<p>Test</p>",
            }
        )
        cls.notification_template = cls.env["pms.notification.template"].create(
            {
                "name": "Rule pre-domain test",
                "code": "rule_pre_domain_test_template",
                "model_id": cls.folio_model.id,
                "mail_template_id": cls.mail_template.id,
            }
        )
        cls.rule = cls.env["pms.property.notification.rule"].create(
            {
                "name": "Rule pre-domain test",
                "template_id": cls.notification_template.id,
                "target_model_id": cls.folio_model.id,
                "rule_type": "event",
                "event_type": "on_write",
                "event_pre_domain": "[('state','=','draft')]",
                "event_domain": "[('state','=','confirm')]",
                "channel": "email",
                "send_immediately": False,
            }
        )

    def _count_rule_logs(self, folio):
        return self.env["pms.notification.log"].search_count(
            [
                ("rule_id", "=", self.rule.id),
                ("origin_model", "=", "pms.folio"),
                ("origin_res_id", "=", folio.id),
            ]
        )

    def test_on_write_uses_event_pre_domain(self):
        folio = self.env["pms.folio"].create(
            {
                "pms_property_id": self.pms_property1.id,
                "partner_name": "Rule Test Guest",
            }
        )

        self.assertEqual(self._count_rule_logs(folio), 0)

        # pre-domain matches (draft), but post-domain does not (still draft)
        folio.write({"partner_name": "Rule Test Guest Updated"})
        self.assertEqual(self._count_rule_logs(folio), 0)

        # pre-domain matches (draft) and post-domain matches (confirm)
        folio.write({"state": "confirm"})
        self.assertEqual(self._count_rule_logs(folio), 1)

        # now pre-domain no longer matches (record is already confirm)
        folio.write({"partner_name": "Rule Test Guest Confirmed"})
        self.assertEqual(self._count_rule_logs(folio), 1)

    def test_pre_domain_restricted_to_on_write(self):
        with self.assertRaises(ValidationError):
            self.env["pms.property.notification.rule"].create(
                {
                    "name": "Invalid pre-domain on create",
                    "template_id": self.notification_template.id,
                    "target_model_id": self.folio_model.id,
                    "rule_type": "event",
                    "event_type": "on_create",
                    "event_pre_domain": "[('state','=','draft')]",
                    "event_domain": "[('state','=','confirm')]",
                    "channel": "email",
                }
            )
