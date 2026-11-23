# Copyright 2021 Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import logging
import xmlrpc.client
from unittest import mock

from odoo.tests import tagged

from . import common, server

_logger = logging.getLogger(__name__)


# post_install so the accounting set up by other modules (chart of accounts and
# its payment method lines) is already in place when the backend is created.
@tagged("post_install", "-at_install")
class TestWubookConnectorAuth(common.TestWubookConnector):
    """Authentication flow of the adapter ``_exec`` method.

    WuBook deprecated the ``acquire_token``/``release_token`` flow in favour of
    a permanent token. The adapter must use the permanent token directly when
    configured, and only fall back to the legacy flow otherwise.
    """

    @mock.patch.object(xmlrpc.client, "Server")
    def test_exec_uses_permanent_token(self, mock_xmlrpc_client_server):
        """
        PRE:    - backend has a permanent token configured
        ACT:    - an API call is performed (search_read -> fetch_rooms)
        POST:   - acquire_token is not called
                - release_token is not called
                - the permanent token is forwarded as the token argument
        """
        # mock object: the property must exist so fetch_rooms returns a result
        mock_server = server.MockWubookServer()
        mock_server.data[self.fake_credentials["property_code"]] = {}
        m = mock_server.get_mock()
        mock_xmlrpc_client_server.return_value = m

        # record the token forwarded to the wired function
        captured = {}
        original_fetch_rooms = m.fetch_rooms

        def recording_fetch_rooms(token, lcode, **kwargs):
            captured["token"] = token
            return original_fetch_rooms(token, lcode, **kwargs)

        m.fetch_rooms = recording_fetch_rooms

        # ARRANGE
        backend = self._build_backend(permanent_token="PERMANENT-TOKEN-XYZ")
        adapter = self._get_adapter(backend)

        # ACT
        adapter.search_read([])

        # ASSERT
        with self.subTest():
            m.acquire_token.assert_not_called()
        with self.subTest():
            m.release_token.assert_not_called()
        with self.subTest():
            self.assertEqual(
                captured.get("token"),
                "PERMANENT-TOKEN-XYZ",
                "The permanent token should be forwarded to the wired function",
            )

    @mock.patch.object(xmlrpc.client, "Server")
    def test_exec_legacy_acquire_token(self, mock_xmlrpc_client_server):
        """
        PRE:    - backend has no permanent token (only username/password/pkey)
        ACT:    - an API call is performed (search_read -> fetch_rooms)
        POST:   - acquire_token is called once (legacy flow)
                - release_token is called once (legacy flow)
        """
        # mock object: the property must exist so fetch_rooms returns a result
        mock_server = server.MockWubookServer()
        mock_server.data[self.fake_credentials["property_code"]] = {}
        m = mock_server.get_mock()
        mock_xmlrpc_client_server.return_value = m

        # ARRANGE (fake_credentials carry no permanent token)
        backend = self._build_backend()
        adapter = self._get_adapter(backend)

        # ACT
        adapter.search_read([])

        # ASSERT
        with self.subTest():
            m.acquire_token.assert_called_once_with(
                backend.username, backend.password, backend.pkey
            )
        with self.subTest():
            m.release_token.assert_called_once()
