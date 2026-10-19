# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""This login cannot ask anyone for a verification code, so it must not let an
account that is protected by one through on its password alone. Those accounts
belong to the login that can ask, and everyone else keeps logging in here.
"""
import base64
import os
from unittest.mock import patch

import werkzeug.exceptions

from odoo.tests import tagged

from odoo.addons.base_rest.controllers.main import _PseudoCollection
from odoo.addons.component.core import WorkContext
from odoo.addons.pms.tests.common import TestPms

IMAGE_URL_BUILDER = (
    "odoo.addons.pms_api_rest.services.pms_login_service.url_image_pms_api_rest"
)


@tagged("post_install", "-at_install")
class TestLoginSecondFactor(TestPms):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.api_user = cls.env["res.users"].create(
            {
                "name": "Api Caller",
                "login": "api.caller@example.org",
                "password": "supersecret",
                "company_id": cls.company1.id,
                "company_ids": [(6, 0, [cls.company1.id])],
                "pms_property_ids": [(6, 0, [cls.pms_property1.id])],
                "pms_property_id": cls.pms_property1.id,
            }
        )

    def _login(self):
        collection = _PseudoCollection("pms.services", self.env)
        work = WorkContext(
            model_name="rest.service.registration", collection=collection
        )
        credentials = self.env.datamodels["pms.api.rest.user.input"](
            username="api.caller@example.org", password="supersecret"
        )
        with patch(IMAGE_URL_BUILDER, return_value=""):
            return work.component(usage="login").login(credentials)

    def test_an_account_without_a_second_factor_still_logs_in(self):
        self.assertTrue(self._login().token)

    def test_an_account_with_a_second_factor_is_refused(self):
        self.api_user.sudo().totp_secret = base64.b32encode(os.urandom(20)).decode()

        with self.assertRaises(werkzeug.exceptions.Unauthorized):
            self._login()
