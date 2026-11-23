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
    """Concurrent calls to WuBook through the adapter ``_exec`` method.

    WuBook allows a single concurrent call per property and rejects the
    overlapping ones with "too many concurrent calls". The adapter locks the
    property during each call, so the jobs of a property do not call WuBook
    at the same time, and retries the job when the property is busy or
    WuBook rejects the call anyway.
    """

    def _get_wubook_adapter(self, mock_xmlrpc_client_server, res=0, data="Ok"):
        m = mock.Mock()
        m.update_avail.return_value = (res, data)
        mock_xmlrpc_client_server.return_value = m
        backend = self._build_backend(permanent_token="PERMANENT-TOKEN-XYZ")
        return self._get_adapter(backend), m

    def _update_avail(self, adapter):
        dfrom = fields.Date.today().strftime(adapter._date_format)
        return adapter._exec("update_avail", dfrom, [])

    def _property_lock_is_free(self, adapter):
        """Whether another job (another connection) can lock the property."""
        key = adapter._property_lock_key()
        with self.registry.cursor() as other_cr:
            other_cr.execute("SELECT pg_try_advisory_lock(%s)", (key,))
            acquired = other_cr.fetchone()[0]
            if acquired:
                other_cr.execute("SELECT pg_advisory_unlock(%s)", (key,))
        return acquired

    @mock.patch.object(xmlrpc.client, "Server")
    def test_exec_property_locked_retries_job(self, mock_xmlrpc_client_server):
        """
        PRE:    - another job is calling WuBook for the same property
        ACT:    - the adapter executes update_avail
        POST:   - WuBook is not called
                - a RetryableJobError is raised, so the job is retried
                - the retry is delayed between 10 and 60 seconds
        """
        adapter, m = self._get_wubook_adapter(mock_xmlrpc_client_server)
        key = adapter._property_lock_key()
        with self.registry.cursor() as other_cr:
            # try-lock: a lock leaked by a previous call must fail the test,
            # not hang it
            other_cr.execute("SELECT pg_try_advisory_lock(%s)", (key,))
            self.assertTrue(other_cr.fetchone()[0], "The property is already locked")
            try:
                with self.assertRaises(RetryableJobError) as cm:
                    self._update_avail(adapter)
            finally:
                other_cr.execute("SELECT pg_advisory_unlock(%s)", (key,))
        with self.subTest():
            m.update_avail.assert_not_called()
        with self.subTest():
            self.assertIsInstance(cm.exception, ChannelAdapterError)
        with self.subTest():
            self.assertTrue(10 <= cm.exception.seconds <= 60)

    @mock.patch.object(xmlrpc.client, "Server")
    def test_exec_releases_property_lock(self, mock_xmlrpc_client_server):
        """
        PRE:    - no other job is calling WuBook for the property
        ACT:    - the adapter executes update_avail, which succeeds, is
                  rejected by WuBook or raises a network error
        POST:   - WuBook is called each time
                - the property is unlocked after each call
        """
        adapter, m = self._get_wubook_adapter(mock_xmlrpc_client_server)
        self._update_avail(adapter)
        with self.subTest("success"):
            self.assertTrue(self._property_lock_is_free(adapter))

        m.update_avail.return_value = (1, "Some other WuBook error")
        with self.assertRaises(ChannelAdapterError):
            self._update_avail(adapter)
        with self.subTest("rejected by WuBook"):
            self.assertTrue(self._property_lock_is_free(adapter))

        m.update_avail.side_effect = ConnectionResetError()
        with self.assertRaises(ConnectionResetError):
            self._update_avail(adapter)
        with self.subTest("network error"):
            self.assertTrue(self._property_lock_is_free(adapter))
        self.assertEqual(m.update_avail.call_count, 3)

    @mock.patch.object(xmlrpc.client, "Server")
    def test_exec_concurrent_calls_retries_job(self, mock_xmlrpc_client_server):
        """
        PRE:    - WuBook rejects update_avail with "too many concurrent calls"
                  (e.g. a call made outside the jobs of this database)
        ACT:    - the adapter executes update_avail
        POST:   - a RetryableJobError is raised, so the job is retried
                - it is still a ChannelAdapterError for the existing cleanups
                - the retry is delayed between 10 and 60 seconds
        """
        adapter, _m = self._get_wubook_adapter(
            mock_xmlrpc_client_server,
            1,
            "Too many concurrent calls for the same property",
        )
        with self.assertRaises(RetryableJobError) as cm:
            self._update_avail(adapter)
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
        adapter, _m = self._get_wubook_adapter(
            mock_xmlrpc_client_server, 1, "Some other WuBook error"
        )
        with self.assertRaises(ChannelAdapterError) as cm:
            self._update_avail(adapter)
        self.assertNotIsInstance(cm.exception, RetryableJobError)
