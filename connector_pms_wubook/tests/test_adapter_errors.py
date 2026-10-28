# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import xmlrpc.client
from unittest import mock

from odoo import fields
from odoo.tests import tagged

from odoo.addons.connector_pms.components.adapter import ChannelAdapterError
from odoo.addons.queue_job.exception import RetryableJobError

from . import common


# post_install so the accounting set up by other modules (chart of accounts and
# its payment method lines) is already in place when the backend is created.
@tagged("post_install", "-at_install")
class TestWubookAdapterErrors(common.TestWubookConnector):
    """Errors returned by WuBook through the adapter ``_exec`` method.

    WuBook allows a single concurrent call per property. The overlapping
    ones are rejected with "too many concurrent calls", which is transient:
    the job must be retried instead of being left failed.
    """

    def _exec_update_avail(self, mock_xmlrpc_client_server, res, data):
        m = mock.Mock()
        m.update_avail.return_value = (res, data)
        mock_xmlrpc_client_server.return_value = m
        backend = self._build_backend(permanent_token="PERMANENT-TOKEN-XYZ")
        adapter = self._get_adapter(backend)
        dfrom = fields.Date.today().strftime(adapter._date_format)
        return adapter._exec("update_avail", dfrom, [])

    @mock.patch.object(xmlrpc.client, "Server")
    def test_exec_concurrent_calls_retries_job(self, mock_xmlrpc_client_server):
        """
        PRE:    - WuBook rejects update_avail with "too many concurrent calls"
        ACT:    - the adapter executes update_avail
        POST:   - a RetryableJobError is raised, so the job is retried
                - it is still a ChannelAdapterError for the existing cleanups
                - the retry is delayed between 10 and 60 seconds
        """
        with self.assertRaises(RetryableJobError) as cm:
            self._exec_update_avail(
                mock_xmlrpc_client_server,
                1,
                "Too many concurrent calls for the same property",
            )
        with self.subTest():
            self.assertIsInstance(cm.exception, ChannelAdapterError)
        with self.subTest():
            self.assertTrue(10 <= cm.exception.seconds <= 60)

    @mock.patch.object(xmlrpc.client, "Server")
    def test_exec_other_error_is_not_retried(self, mock_xmlrpc_client_server):
        """
        PRE:    - WuBook rejects update_avail with any other error
        ACT:    - the adapter executes update_avail
        POST:   - a ChannelAdapterError is raised, and it is not retryable
        """
        with self.assertRaises(ChannelAdapterError) as cm:
            self._exec_update_avail(
                mock_xmlrpc_client_server, 1, "Some other WuBook error"
            )
        self.assertNotIsInstance(cm.exception, RetryableJobError)
