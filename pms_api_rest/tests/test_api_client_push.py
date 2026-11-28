# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""Pushes of availability, prices and rules to the PMS API clients.

A client without the endpoint of a call type does not receive that call
(an integration that has been switched off keeps its user, but its URLs
are emptied), and a client that fails must not stop the push to the
other clients.
"""
from datetime import timedelta
from unittest import mock

import requests

from odoo import fields
from odoo.tests import tagged

from odoo.addons.pms.tests.common import TestPms

REQUESTS_POST = "odoo.addons.pms_api_rest.models.pms_property.requests.post"


def ok_response(*args, **kwargs):
    return mock.Mock(ok=True, status_code=200, text="OK")


@tagged("post_install", "-at_install")
class TestApiClientPush(TestPms):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Only the clients of this test receive pushes
        cls.env["res.users"].search([("pms_api_client", "=", True)]).write(
            {"pms_api_client": False}
        )
        cls.room_type_class1.pms_property_ids = [(6, 0, cls.pms_property1.ids)]
        cls.room_type = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [(6, 0, cls.pms_property1.ids)],
                "name": "Double Push Test",
                "default_code": "DBL_PUSH",
                "class_id": cls.room_type_class1.id,
                "list_price": 50,
            }
        )
        cls.env["pms.room"].create(
            {
                "pms_property_id": cls.pms_property1.id,
                "name": "Room 401",
                "room_type_id": cls.room_type.id,
                "capacity": 2,
            }
        )
        cls.client_a = cls._create_api_client("API Client A", "https://a.example")
        cls.client_b = cls._create_api_client("API Client B", "https://b.example")

    @classmethod
    def _create_api_client(cls, name, base_url):
        client = cls.env["res.users"].create(
            {
                "name": name,
                "login": name.lower().replace(" ", "_"),
                "company_id": cls.company1.id,
                "company_ids": [(6, 0, cls.company1.ids)],
                "pms_property_id": cls.pms_property1.id,
                "pms_property_ids": [(6, 0, cls.pms_property1.ids)],
                "pms_api_client": True,
                "url_endpoint_availability": f"{base_url}/availability",
                "url_endpoint_prices": f"{base_url}/prices",
                "url_endpoint_rules": f"{base_url}/rules",
            }
        )
        cls.env["ota.property.settings"].create(
            {
                "pms_property_id": cls.pms_property1.id,
                "agency_id": client.partner_id.id,
                "main_avail_plan_id": cls.availability_plan1.id,
                "main_pricelist_id": cls.pricelist1.id,
            }
        )
        return client

    def _create_rule(self):
        return self.env["pms.availability.plan.rule"].create(
            {
                "availability_plan_id": self.availability_plan1.id,
                "pms_property_id": self.pms_property1.id,
                "room_type_id": self.room_type.id,
                "date": fields.Date.today(),
                "min_stay": 2,
            }
        )

    def _push_logs(self, client):
        return self.env["pms.api.log"].search(
            [("client_id", "=", client.id), ("method", "=", "PUSH")]
        )

    def test_push_batch_skips_client_without_endpoint(self):
        self.client_b.url_endpoint_availability = False
        today = fields.Date.today()
        with mock.patch(REQUESTS_POST, side_effect=ok_response) as post:
            self.env["pms.property"].pms_api_push_batch(
                "availability",
                date_from=fields.Date.to_string(today),
                date_to=fields.Date.to_string(today + timedelta(days=2)),
            )
        self.assertEqual(
            [call.args[0] for call in post.call_args_list],
            [self.client_a.url_endpoint_availability],
        )
        self.assertEqual(self._push_logs(self.client_a).mapped("status"), ["success"])
        self.assertFalse(self._push_logs(self.client_b))

    def test_rule_push_skips_client_without_endpoint(self):
        self.client_b.url_endpoint_rules = False
        self._create_rule()
        with mock.patch(REQUESTS_POST, side_effect=ok_response) as post:
            self.env["pms.property"].pms_cron_rule_push()
        self.assertEqual(
            [call.args[0] for call in post.call_args_list],
            [self.client_a.url_endpoint_rules],
        )
        self.assertEqual(self._push_logs(self.client_a).mapped("status"), ["success"])
        self.assertFalse(self._push_logs(self.client_b))

    def test_rule_push_goes_on_when_a_client_fails(self):
        self._create_rule()

        def post(url, **kwargs):
            if url == self.client_a.url_endpoint_rules:
                raise requests.ConnectionError("Connection refused")
            return ok_response()

        with mock.patch(REQUESTS_POST, side_effect=post) as mocked:
            self.env["pms.property"].pms_cron_rule_push()
        self.assertEqual(mocked.call_count, 2)
        log_a = self._push_logs(self.client_a)
        self.assertEqual(log_a.mapped("status"), ["error"])
        self.assertIn("Connection refused", log_a.response)
        self.assertEqual(self._push_logs(self.client_b).mapped("status"), ["success"])
