import hashlib
import hmac
from datetime import timedelta
from unittest import mock

import requests

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.queue_job.exception import RetryableJobError
from odoo.addons.queue_job.tests.common import trap_jobs

from ..models.host_link import (
    PARAM_INSTANCE,
    PARAM_SECRET,
    PARAM_URL,
    TECHNICAL_LOGIN,
)

POST = "odoo.addons.roomdoo_host_link.models.host_link.requests.post"


@tagged("post_install", "-at_install")
class TestHostLink(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param(PARAM_URL, "https://host.example.com/")
        icp.set_param(PARAM_SECRET, "s3cret")
        icp.set_param(PARAM_INSTANCE, "hotelone")
        cls.link = cls.env["roomdoo.host.link"]
        cls.prop = cls.env["pms.property"].search([], limit=1)

    @staticmethod
    def _events(trap):
        return [job.args[0] for job in trap.enqueued_jobs]

    def test_nothing_happens_without_configuration(self):
        icp = self.env["ir.config_parameter"].sudo()
        for param in (PARAM_URL, PARAM_INSTANCE):
            value = icp.get_param(param)
            icp.set_param(param, "")
            with trap_jobs() as trap:
                self.prop.write({"name": "Renamed hotel"})
            trap.assert_jobs_count(0)
            icp.set_param(param, value)

    def test_a_changed_hotel_is_announced(self):
        with trap_jobs() as trap:
            self.prop.write({"name": "Renamed hotel"})
        events = self._events(trap)
        self.assertEqual([e["kind"] for e in events], ["properties"])
        self.assertEqual(events[0]["data"][0]["name"], "Renamed hotel")
        self.assertEqual(events[0]["db"], self.env.cr.dbname)
        self.assertEqual(events[0]["instance"], "hotelone")

    def test_other_changes_of_a_hotel_are_not(self):
        with trap_jobs() as trap:
            self.prop.write({"street": "Somewhere 1"})
        trap.assert_jobs_count(0)

    def test_only_internal_users_are_announced(self):
        with trap_jobs() as trap:
            new_test_user(self.env, login="portal-guest", groups="base.group_portal")
        trap.assert_jobs_count(0)
        with trap_jobs() as trap:
            new_test_user(self.env, login="front-desk", groups="base.group_user")
        events = self._events(trap)
        self.assertEqual(events[0]["kind"], "users")
        self.assertEqual(events[0]["data"][0]["login"], "front-desk")

    def test_flags_are_sent_whole(self):
        with trap_jobs() as trap:
            self.env["feature.flag"].create({"name": "SOME_FLAG"})
        events = self._events(trap)
        self.assertEqual(events[-1]["kind"], "flags")
        self.assertIn("SOME_FLAG", [f["name"] for f in events[-1]["data"]])

    def test_events_are_signed_with_the_instance_secret(self):
        sent = {}

        def fake_post(url, data, headers, timeout):
            sent.update(url=url, data=data, headers=headers)
            return mock.Mock(status_code=200, text="ok")

        with mock.patch(POST, side_effect=fake_post):
            self.link._send({"id": "e1", "kind": "heartbeat", "data": {}})
        self.assertEqual(
            sent["url"], "https://host.example.com/roomdoo/registry/events"
        )
        timestamp = sent["headers"]["X-Roomdoo-Timestamp"]
        expected = hmac.new(
            b"s3cret", timestamp.encode() + b"." + sent["data"], hashlib.sha256
        ).hexdigest()
        self.assertEqual(sent["headers"]["X-Roomdoo-Signature"], f"sha256={expected}")

    def test_a_down_host_is_retried_a_refusal_is_not(self):
        with mock.patch(POST, side_effect=requests.ConnectionError("down")):
            with self.assertRaises(RetryableJobError):
                self.link._send({"id": "e2"})
        for status in (503, 404):
            answer = mock.Mock(status_code=status, text="")
            with mock.patch(POST, return_value=answer):
                with self.assertRaises(RetryableJobError):
                    self.link._send({"id": "e3"})
        refused = mock.Mock(status_code=401, text="bad signature")
        with mock.patch(POST, return_value=refused):
            with self.assertRaises(UserError):
                self.link._send({"id": "e4"})

    def test_usage_leaves_out_the_technical_users_logins(self):
        technical = self.link._ensure_technical_user()
        someone = new_test_user(self.env, login="someone", groups="base.group_user")
        logs = self.env["res.users.log"].sudo()
        theirs = logs.with_user(someone).create({})
        ours = logs.with_user(technical).create({})
        earlier = fields.Datetime.now() - timedelta(days=2)
        self.env.cr.execute(
            "UPDATE res_users_log SET create_date = %s WHERE id = %s",
            (earlier, theirs.id),
        )
        self.env.cr.execute(
            "UPDATE res_users_log SET create_date = now() WHERE id = %s", (ours.id,)
        )
        logs.invalidate_model(["create_date"])
        usage = self.link._usage()
        self.assertEqual(usage["last_login_at"], fields.Datetime.to_string(earlier))
        self.assertEqual(usage["hotels"], self.env["pms.property"].search_count([]))

    def test_versions_are_sent_only_when_they_change(self):
        with trap_jobs() as trap:
            self.link._cron_tick()
        self.assertEqual(
            sorted(e["kind"] for e in self._events(trap)), ["heartbeat", "versions"]
        )
        with trap_jobs() as trap:
            self.link._cron_tick()
        trap.assert_jobs_count(0)

    def test_the_technical_user_is_adopted_never_duplicated(self):
        first = self.link._ensure_technical_user()
        again = self.link._ensure_technical_user()
        self.assertEqual(first, again)
        users = self.env["res.users"].with_context(active_test=False)
        self.assertEqual(users.search_count([("login", "=", TECHNICAL_LOGIN)]), 1)
        self.assertTrue(first.has_group("roomdoo_host_link.group_host_link"))

    def test_the_technical_user_may_switch_flags_others_may_not(self):
        technical = self.link._ensure_technical_user()
        flag = (
            self.env["feature.flag"].with_user(technical).create({"name": "FROM_HOST"})
        )
        self.assertTrue(flag)
        clerk = new_test_user(self.env, login="clerk", groups="base.group_user")
        with self.assertRaises(AccessError):
            self.env["feature.flag"].with_user(clerk).create({"name": "NOT_ALLOWED"})
