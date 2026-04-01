from datetime import timedelta
from unittest.mock import patch

from odoo import fields
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
        _eligibility = (
            "odoo.addons.pms_notifications.models"
            ".pms_property_notification_rule"
            ".PmsPropertyNotificationRule._is_origin_record_eligible"
        )
        with patch(_eligibility, return_value=True):
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


class TestEventDelayMinutes(TestPms):
    """Tests for the event_delay_minutes feature on event-based rules."""

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
                "name": "Delay test template",
                "model_id": cls.folio_model.id,
                "subject": "Test delay",
                "body_html": "<p>Delay test</p>",
            }
        )
        cls.notification_template = cls.env["pms.notification.template"].create(
            {
                "name": "Delay test notification",
                "code": "delay_test_notification",
                "model_id": cls.folio_model.id,
                "mail_template_id": cls.mail_template.id,
            }
        )
        # Rule: on_create, always-match domain, 30 min delay
        cls.delay_rule = cls.env["pms.property.notification.rule"].create(
            {
                "name": "Delay rule test",
                "template_id": cls.notification_template.id,
                "target_model_id": cls.folio_model.id,
                "rule_type": "event",
                "event_type": "on_create",
                "event_domain": "[]",
                "channel": "email",
                "send_immediately": False,
                "event_delay_minutes": 30,
            }
        )

    def _create_folio(self, partner_name):
        return self.env["pms.folio"].create(
            {
                "pms_property_id": self.pms_property1.id,
                "partner_name": partner_name,
            }
        )

    def _get_delay_rule_logs(self, folio):
        return self.env["pms.notification.log"].search(
            [
                ("rule_id", "=", self.delay_rule.id),
                ("origin_model", "=", "pms.folio"),
                ("origin_res_id", "=", folio.id),
            ]
        )

    def _create_pending_log(self, folio, rule=None, scheduled_date=None):
        return (
            self.env["pms.notification.log"]
            .sudo()
            .create(
                {
                    "name": "Test log",
                    "state": "pending",
                    "template_id": self.notification_template.id,
                    "rule_id": (rule or self.delay_rule).id,
                    "channel": "email",
                    "scheduled_date": scheduled_date,
                    "origin_model": "pms.folio",
                    "origin_res_id": folio.id,
                    "recipient_mode": "template",
                }
            )
        )

    def test_delay_sets_scheduled_date_on_log(self):
        _eligibility = (
            "odoo.addons.pms_notifications.models"
            ".pms_property_notification_rule"
            ".PmsPropertyNotificationRule._is_origin_record_eligible"
        )
        with patch(_eligibility, return_value=True):
            folio = self._create_folio("Delay Guest A")
        logs = self._get_delay_rule_logs(folio)
        self.assertEqual(len(logs), 1)
        log = logs[0]
        self.assertEqual(log.state, "pending")
        self.assertTrue(log.scheduled_date)
        expected = fields.Datetime.now() + timedelta(minutes=30)
        delta = abs((log.scheduled_date - expected).total_seconds())
        self.assertLess(delta, 30)

    def test_delay_suppresses_send_immediately(self):
        rule = self.env["pms.property.notification.rule"].create(
            {
                "name": "Delay + send_immediately test",
                "template_id": self.notification_template.id,
                "target_model_id": self.folio_model.id,
                "rule_type": "event",
                "event_type": "on_create",
                "event_domain": "[]",
                "channel": "email",
                "send_immediately": True,
                "event_delay_minutes": 10,
            }
        )
        _eligibility = (
            "odoo.addons.pms_notifications.models"
            ".pms_property_notification_rule"
            ".PmsPropertyNotificationRule._is_origin_record_eligible"
        )
        with patch(_eligibility, return_value=True):
            folio = self._create_folio("Delay Immediate Guest")
        logs = self.env["pms.notification.log"].search(
            [
                ("rule_id", "=", rule.id),
                ("origin_res_id", "=", folio.id),
            ]
        )
        self.assertEqual(len(logs), 1)
        # Must NOT be sent despite send_immediately=True
        self.assertEqual(logs.state, "pending")
        self.assertTrue(logs.scheduled_date)

    def test_batch_does_not_pick_up_future_scheduled_date(self):
        folio = self._create_folio("Future Log Guest")
        future = fields.Datetime.now() + timedelta(minutes=60)
        log = self._create_pending_log(folio, scheduled_date=future)
        self.env["pms.notification.log"].action_send_pending_batch()
        log.invalidate_recordset()
        self.assertEqual(log.state, "pending")

    def test_batch_skips_when_domain_no_longer_matches(self):
        rule = self.env["pms.property.notification.rule"].create(
            {
                "name": "Domain skip test rule",
                "template_id": self.notification_template.id,
                "target_model_id": self.folio_model.id,
                "rule_type": "event",
                "event_type": "on_create",
                "event_domain": "[('state','=','confirm')]",
                "channel": "email",
                "send_immediately": False,
                "event_delay_minutes": 1,
            }
        )
        folio = self._create_folio("Skip Domain Guest")
        # folio is in draft → domain [state=confirm] does not match
        past = fields.Datetime.now() - timedelta(minutes=1)
        log = self._create_pending_log(folio, rule=rule, scheduled_date=past)
        self.env["pms.notification.log"].action_send_pending_batch()
        log.invalidate_recordset()
        self.assertEqual(log.state, "skipped")

    def test_batch_skips_when_origin_record_missing(self):
        past = fields.Datetime.now() - timedelta(minutes=1)
        log = (
            self.env["pms.notification.log"]
            .sudo()
            .create(
                {
                    "name": "Missing origin log",
                    "state": "pending",
                    "template_id": self.notification_template.id,
                    "rule_id": self.delay_rule.id,
                    "channel": "email",
                    "scheduled_date": past,
                    "origin_model": "pms.folio",
                    "origin_res_id": 999999,  # non-existent
                    "recipient_mode": "template",
                }
            )
        )
        self.env["pms.notification.log"].action_send_pending_batch()
        log.invalidate_recordset()
        self.assertEqual(log.state, "skipped")

    def test_batch_does_not_skip_when_domain_matches(self):
        folio = self._create_folio("Domain Match Guest")
        past = fields.Datetime.now() - timedelta(minutes=1)
        log = self._create_pending_log(folio, scheduled_date=past)
        with patch.object(
            type(log),
            "action_send_by_channel",
            return_value=True,
        ):
            self.env["pms.notification.log"].action_send_pending_batch()
        log.invalidate_recordset()
        self.assertNotEqual(log.state, "skipped")

    def test_skipped_log_does_not_count_toward_max_sends(self):
        rule = self.env["pms.property.notification.rule"].create(
            {
                "name": "Max sends + delay test",
                "template_id": self.notification_template.id,
                "target_model_id": self.folio_model.id,
                "rule_type": "event",
                "event_type": "on_create",
                "event_domain": "[]",
                "channel": "email",
                "send_immediately": False,
                "event_delay_minutes": 1,
                "max_sends_per_record": 1,
            }
        )
        folio = self._create_folio("Max Sends Delay Guest")
        self.env["pms.notification.log"].sudo().create(
            {
                "name": "Skipped log",
                "state": "skipped",
                "template_id": self.notification_template.id,
                "rule_id": rule.id,
                "channel": "email",
                "origin_model": "pms.folio",
                "origin_res_id": folio.id,
                "recipient_mode": "template",
            }
        )
        self.assertTrue(rule._is_under_max_sends(folio))

    def test_zero_delay_behaves_as_before(self):
        rule = self.env["pms.property.notification.rule"].create(
            {
                "name": "Zero delay backward compat",
                "template_id": self.notification_template.id,
                "target_model_id": self.folio_model.id,
                "rule_type": "event",
                "event_type": "on_create",
                "event_domain": "[]",
                "channel": "email",
                "send_immediately": False,
                "event_delay_minutes": 0,
            }
        )
        _eligibility = (
            "odoo.addons.pms_notifications.models"
            ".pms_property_notification_rule"
            ".PmsPropertyNotificationRule._is_origin_record_eligible"
        )
        with patch(_eligibility, return_value=True):
            folio = self._create_folio("Zero Delay Guest")
        logs = self.env["pms.notification.log"].search(
            [
                ("rule_id", "=", rule.id),
                ("origin_res_id", "=", folio.id),
            ]
        )
        self.assertEqual(len(logs), 1)
        self.assertFalse(logs.scheduled_date)
        self.assertEqual(logs.state, "pending")

    def test_constraint_negative_delay_raises(self):
        with self.assertRaises(ValidationError):
            self.env["pms.property.notification.rule"].create(
                {
                    "name": "Negative delay",
                    "template_id": self.notification_template.id,
                    "target_model_id": self.folio_model.id,
                    "rule_type": "event",
                    "event_type": "on_create",
                    "event_domain": "[]",
                    "channel": "email",
                    "event_delay_minutes": -5,
                }
            )

    def test_constraint_delay_on_scheduled_rule_raises(self):
        with self.assertRaises(ValidationError):
            self.env["pms.property.notification.rule"].create(
                {
                    "name": "Delay on scheduled",
                    "template_id": self.notification_template.id,
                    "target_model_id": self.folio_model.id,
                    "rule_type": "scheduled",
                    "time_field_name": "create_date",
                    "scheduled_domain": "[]",
                    "channel": "email",
                    "event_delay_minutes": 10,
                }
            )
