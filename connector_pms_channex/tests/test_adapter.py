# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""The REST adapter: pagination, domain splitting, and above all how each kind
of failure is classified. Getting that wrong either fills the queue with jobs
that can never succeed, or silently drops updates that only needed a retry."""

from odoo.tests.common import tagged

from odoo.addons.connector.exception import IDMissingInBackend
from odoo.addons.connector_pms_channex.components.adapter import ChannexAPIError
from odoo.addons.queue_job.exception import RetryableJobError

from .common import ChannexConnectorCase

PROPERTIES = "channel.channex.pms.property"


@tagged("post_install", "-at_install")
class TestChannexAdapter(ChannexConnectorCase):
    def test_read_unwraps_attributes(self):
        self.server.seed(
            "properties", [{"id": "p-1", "title": "Hotel", "currency": "EUR"}]
        )
        record = self._adapter(PROPERTIES).read("p-1")
        # Flat dict: id lifted out of data, attributes merged in.
        self.assertEqual(record["id"], "p-1")
        self.assertEqual(record["title"], "Hotel")

    def test_pagination_walks_every_page(self):
        self.backend.page_limit = 2
        self.server.seed(
            "properties",
            [{"id": f"p-{n}", "title": f"H{n}", "currency": "EUR"} for n in range(5)],
        )
        records = self._adapter(PROPERTIES).search_read([])
        self.assertEqual(len(records), 5)
        self.assertEqual(len(self.server.calls_to("GET", "properties")), 3)

    def test_domain_split_between_server_and_memory(self):
        self.server.seed(
            "properties",
            [
                {"id": "p-1", "title": "Wanted", "currency": "EUR"},
                {"id": "p-2", "title": "Other", "currency": "EUR"},
            ],
        )
        # ``title`` is a server filter, ``currency`` is not: the first narrows
        # the query, the second is applied in memory.
        records = self._adapter(PROPERTIES).search_read(
            [("title", "=", "Wanted"), ("currency", "=", "EUR")]
        )
        self.assertEqual([r["id"] for r in records], ["p-1"])
        _method, _path, _body, params = self.server.calls_to("GET", "properties")[0]
        self.assertEqual(params["filter[title]"], "Wanted")
        self.assertNotIn("filter[currency]", params)

    # -- failure taxonomy --------------------------------------------------

    def test_validation_error_is_permanent_and_readable(self):
        self.server.fail_next(
            422,
            {
                "errors": {
                    "code": "validation",
                    "title": "Missing required fields",
                    "details": {"title": ["can't be blank"]},
                }
            },
        )
        with self.assertRaises(ChannexAPIError) as caught:
            self._adapter(PROPERTIES).create({"currency": "EUR"})
        message = str(caught.exception)
        self.assertIn("Missing required fields", message)
        self.assertIn("validation", message)
        # The offending field has to survive into the job, or nobody can fix it.
        self.assertIn("title", message)

    def test_auth_error_is_permanent(self):
        self.server.fail_next(401)
        with self.assertRaises(ChannexAPIError):
            self._adapter(PROPERTIES).search_read([])

    def test_missing_record_maps_to_id_missing(self):
        with self.assertRaises(IDMissingInBackend):
            self._adapter(PROPERTIES).read("nope")

    def test_throttling_is_retryable_and_honours_retry_after(self):
        self.server.fail_next(429, headers={"Retry-After": "7"})
        with self.assertRaises(RetryableJobError) as caught:
            self._adapter(PROPERTIES).search_read([])
        self.assertEqual(caught.exception.seconds, 7)

    def test_server_error_is_retryable(self):
        self.server.fail_next(503)
        with self.assertRaises(RetryableJobError):
            self._adapter(PROPERTIES).search_read([])

    def test_timeout_is_retryable(self):
        self.server.timeout_next()
        with self.assertRaises(RetryableJobError):
            self._adapter(PROPERTIES).search_read([])

    # -- logging and safety valves ----------------------------------------

    def test_call_is_logged_against_the_generic_backend(self):
        self.server.seed(
            "properties", [{"id": "p-1", "title": "Hotel", "currency": "EUR"}]
        )
        self._adapter(PROPERTIES).read("p-1")
        log = self.env["channel.backend.log"].search(
            [("backend_id", "=", self.backend.parent_id.id)]
        )
        self.assertTrue(log)

    def test_api_key_never_reaches_the_log(self):
        self.server.seed(
            "properties", [{"id": "p-1", "title": "Hotel", "currency": "EUR"}]
        )
        self._adapter(PROPERTIES).read("p-1")
        logs = self.env["channel.backend.log"].search([])
        for log in logs:
            self.assertNotIn("test-key", log.arguments or "")
            self.assertNotIn("test-key", log.response or "")

    def test_export_disabled_blocks_writes_but_not_reads(self):
        self.backend.export_disabled = True
        self.server.seed(
            "properties", [{"id": "p-1", "title": "Hotel", "currency": "EUR"}]
        )
        self.assertTrue(self._adapter(PROPERTIES).search_read([]))
        self.assertIsNone(self._adapter(PROPERTIES).create({"title": "New"}))
        self.assertFalse(self.server.calls_to("POST"))

    def test_rate_limit_is_retryable_once_configured(self):
        method = self.env["channel.backend.method"].create(
            {
                "name": "GET /properties",
                "backend_type_id": self.backend.backend_type_id.id,
                "max_calls": 1,
                "time_window": 60,
            }
        )
        self.assertTrue(method)
        self.server.seed(
            "properties", [{"id": "p-1", "title": "Hotel", "currency": "EUR"}]
        )
        self._adapter(PROPERTIES).search_read([])
        with self.assertRaises(RetryableJobError):
            self._adapter(PROPERTIES).search_read([])
