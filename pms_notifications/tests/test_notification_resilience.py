from unittest.mock import patch

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tools import mute_logger

from odoo.addons.pms.tests.common import TestPms
from odoo.addons.pms_notifications.models.pms_property_notification_rule import (
    PmsPropertyNotificationRule,
)


class TestPmsNotificationResilience(TestPms):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.folio_model = cls.env.ref("pms.model_pms_folio")
        cls.env["pms.property.notification.rule"].search([("active", "=", True)]).write(
            {"active": False}
        )

        cls.mail_template = cls.env["mail.template"].create(
            {
                "name": "Notification resilience template",
                "model_id": cls.folio_model.id,
                "subject": "Test",
                "body_html": "<p>Test</p>",
            }
        )
        cls.notification_template = cls.env["pms.notification.template"].create(
            {
                "name": "Notification resilience",
                "code": "notification_resilience",
                "model_id": cls.folio_model.id,
                "mail_template_id": cls.mail_template.id,
            }
        )

    def _create_folio(self, partner_name):
        return self.env["pms.folio"].create(
            {
                "pms_property_id": self.pms_property1.id,
                "partner_name": partner_name,
            }
        )

    def _create_scheduled_rule(self, name):
        return self.env["pms.property.notification.rule"].create(
            {
                "name": name,
                "template_id": self.notification_template.id,
                "target_model_id": self.folio_model.id,
                "rule_type": "scheduled",
                "time_field_name": "create_date",
                "scheduled_domain": "[]",
                "channel": "email",
                "send_immediately": False,
            }
        )

    def test_event_log_failure_does_not_block_origin_create(self):
        rule = self.env["pms.property.notification.rule"].create(
            {
                "name": "Event resilience",
                "template_id": self.notification_template.id,
                "target_model_id": self.folio_model.id,
                "rule_type": "event",
                "event_type": "on_create",
                "event_domain": "[]",
                "channel": "email",
                "send_immediately": False,
            }
        )

        with patch(
            "odoo.addons.pms_notifications.models.pms_notification_log."
            "PmsNotificationLog.create",
            side_effect=ValidationError("forced log creation failure"),
        ):
            folio = self._create_folio("Resilience Event Guest")

        self.assertTrue(folio.exists())

        logs = self.env["pms.notification.log"].search(
            [
                ("rule_id", "=", rule.id),
                ("origin_model", "=", "pms.folio"),
                ("origin_res_id", "=", folio.id),
            ]
        )
        self.assertFalse(logs)

    @mute_logger("odoo.addons.pms_notifications.models.pms_property_notification_rule")
    def test_scheduled_log_failure_does_not_block_other_records(self):
        rule = self._create_scheduled_rule("Scheduled resilience")
        folio_1 = self._create_folio("Scheduled Resilience 1")
        folio_2 = self._create_folio("Scheduled Resilience 2")
        records = folio_1 | folio_2
        now = fields.Datetime.now()
        prop_map = {rid: self.pms_property1 for rid in records.ids}
        original_method = PmsPropertyNotificationRule._scheduled_build_log_vals

        def _raise_for_first_rec(rule_rec, now_dt, rec, prop):
            if rec.id == folio_1.id:
                raise ValidationError("forced scheduled build failure")
            return original_method(rule_rec, now_dt, rec, prop)

        with patch(
            "odoo.addons.pms_notifications.models.pms_property_notification_rule."
            "PmsPropertyNotificationRule._scheduled_build_log_vals",
            autospec=True,
            side_effect=_raise_for_first_rec,
        ), patch(
            "odoo.addons.pms_notifications.models.pms_property_notification_rule."
            "PmsPropertyNotificationRule._is_origin_record_eligible",
            return_value=True,
        ):
            rule._scheduled_create_logs_and_send(now, records, prop_map)

        first_logs = self.env["pms.notification.log"].search_count(
            [
                ("rule_id", "=", rule.id),
                ("origin_model", "=", "pms.folio"),
                ("origin_res_id", "=", folio_1.id),
            ]
        )
        second_logs = self.env["pms.notification.log"].search_count(
            [
                ("rule_id", "=", rule.id),
                ("origin_model", "=", "pms.folio"),
                ("origin_res_id", "=", folio_2.id),
            ]
        )

        self.assertEqual(first_logs, 0)
        self.assertEqual(second_logs, 1)

    @mute_logger("odoo.addons.pms_notifications.models.pms_property_notification_rule")
    def test_run_scheduled_rules_continues_when_one_rule_crashes(self):
        rule_1 = self._create_scheduled_rule("Scheduled rule crash")
        rule_2 = self._create_scheduled_rule("Scheduled rule continue")
        called_rule_ids = []

        def _fake_run(rule_rec, now_dt):
            called_rule_ids.append(rule_rec.id)
            if rule_rec.id == rule_1.id:
                raise ValidationError("forced scheduled rule crash")
            return True

        with patch(
            "odoo.addons.pms_notifications.models.pms_property_notification_rule."
            "PmsPropertyNotificationRule._run_one_scheduled_rule",
            autospec=True,
            side_effect=_fake_run,
        ):
            self.env["pms.property.notification.rule"].run_scheduled_rules()

        self.assertIn(rule_1.id, called_rule_ids)
        self.assertIn(rule_2.id, called_rule_ids)
