from unittest.mock import MagicMock, patch

import requests as req

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import TestBookaiCommon

REQUESTS_POST = "odoo.addons.pms_bookai.wizards." "bookai_register_wizard.requests.post"


@tagged("post_install", "-at_install")
class TestBookaiRegisterWizard(TestBookaiCommon):
    def _create_wizard(self, **kwargs):
        vals = {
            "bookai_base_url": "https://bookai.test",
            "provisioning_key": "prov-key-123",
            "odoo_username": "bookai@test.com",
            "odoo_api_key": "api-key-123",
        }
        vals.update(kwargs)
        return self.env["bookai.register.wizard"].create(vals)

    def test_default_get_loads_config(self):
        self.icp.set_param("pms_bookai.api_endpoint", "https://saved.test")
        self.icp.set_param("pms_bookai.odoo_username", "saved@test.com")
        wizard = self.env["bookai.register.wizard"].new({})
        defaults = wizard.default_get(
            [
                "bookai_base_url",
                "odoo_username",
            ]
        )
        self.assertEqual(
            defaults.get("bookai_base_url"),
            "https://saved.test",
        )

    def test_register_success(self):
        wizard = self._create_wizard()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": "ok",
            "bearer_token": "new-bearer-token",
            "steps": [],
        }
        mock_resp.raise_for_status = MagicMock()
        with patch(REQUESTS_POST, return_value=mock_resp):
            wizard.action_register()
        token = self.icp.get_param("pms_bookai.api_token")
        self.assertEqual(token, "new-bearer-token")

    def test_register_connection_error(self):
        wizard = self._create_wizard()
        with patch(
            REQUESTS_POST,
            side_effect=req.exceptions.ConnectionError,
        ):
            with self.assertRaises(UserError):
                wizard.action_register()

    def test_register_timeout(self):
        wizard = self._create_wizard()
        with patch(
            REQUESTS_POST,
            side_effect=req.exceptions.Timeout,
        ):
            with self.assertRaises(UserError):
                wizard.action_register()

    def test_register_http_error(self):
        wizard = self._create_wizard()
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"
        mock_resp.raise_for_status.side_effect = req.exceptions.HTTPError(
            response=mock_resp
        )
        with patch(REQUESTS_POST, return_value=mock_resp):
            with self.assertRaises(UserError):
                wizard.action_register()

    def test_register_partial_status(self):
        wizard = self._create_wizard()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": "partial",
            "bearer_token": "partial-token",
            "steps": [
                {"step": "sync_properties", "status": "error", "detail": "fail"},
            ],
        }
        mock_resp.raise_for_status = MagicMock()
        with patch(REQUESTS_POST, return_value=mock_resp):
            result = wizard.action_register()
        self.assertEqual(result["params"]["type"], "warning")
