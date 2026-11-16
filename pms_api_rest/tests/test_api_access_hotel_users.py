# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""The PMS APIs are for the people who work at a property and for the API
clients. A portal user without a PMS role, such as a customer who signed up on
a website, is turned away from the login and from every request, and until the
enforce parameter is on that refusal is only logged.
"""
import importlib.util
import os
from unittest.mock import patch

import werkzeug.exceptions

from odoo.tests import tagged

from odoo.addons.auth_jwt.exceptions import UnauthorizedInvalidToken
from odoo.addons.base_rest.controllers.main import _PseudoCollection
from odoo.addons.component.core import WorkContext
from odoo.addons.pms.tests.common import TestPms

from ..models.res_users import API_ACCESS_ENFORCE_PARAM

IMAGE_URL_BUILDER = (
    "odoo.addons.pms_api_rest.services.pms_login_service.url_image_pms_api_rest"
)
ACCESS_LOGGER = "odoo.addons.pms_api_rest.models.res_users"
PASSWORD = "supersecret"


@tagged("post_install", "-at_install")
class TestApiAccessHotelUsers(TestPms):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.portal_group = cls.env.ref("base.group_portal")
        # A customer who signed up on a website: no property at all.
        cls.customer = cls._create_user(
            "customer@example.org", portal=True, at_property=False
        )
        cls.receptionist = cls._create_user(
            "receptionist@example.org", portal=True, pms_api_user_role="receptionist"
        )
        cls.api_client = cls._create_user(
            "channel@example.org", portal=True, pms_api_client=True
        )
        cls.employee = cls._create_user("employee@example.org")
        cls.validator = cls.env["auth.jwt.validator"]._get_validator_by_name("api_pms")

    @classmethod
    def _create_user(cls, login, portal=False, at_property=True, **values):
        values.update(
            {
                "name": login,
                "login": login,
                "password": PASSWORD,
                "company_id": cls.company1.id,
                "company_ids": [(6, 0, [cls.company1.id])],
            }
        )
        if at_property:
            # The login answer carries the default property, so every user
            # that is meant to log in needs one.
            values.update(
                {
                    "pms_property_ids": [(6, 0, [cls.pms_property1.id])],
                    "pms_property_id": cls.pms_property1.id,
                }
            )
        if portal:
            values["groups_id"] = [(6, 0, [cls.portal_group.id])]
        return cls.env["res.users"].with_context(no_reset_password=True).create(values)

    def _enforce(self, enabled=True):
        self.env["ir.config_parameter"].sudo().set_param(
            API_ACCESS_ENFORCE_PARAM, "1" if enabled else "0"
        )

    def _login(self, user):
        collection = _PseudoCollection("pms.services", self.env)
        work = WorkContext(
            model_name="rest.service.registration", collection=collection
        )
        credentials = self.env.datamodels["pms.api.rest.user.input"](
            username=user.login, password=PASSWORD
        )
        with patch(IMAGE_URL_BUILDER, return_value=""):
            return work.component(usage="login").login(credentials)

    def _check_uid(self, user):
        return self.validator._get_and_check_uid({"username": user.login})

    def test_a_new_user_has_no_role(self):
        self.assertFalse(self.customer.pms_api_user_role)
        self.assertFalse(self.employee.pms_api_user_role)

    def test_a_portal_user_without_a_role_cannot_log_in(self):
        self._enforce()
        with self.assertLogs(ACCESS_LOGGER, level="WARNING") as logs:
            with self.assertRaises(werkzeug.exceptions.Unauthorized):
                self._login(self.customer)
        self.assertIn("refused", logs.output[0])

    def test_until_enforced_the_refusal_is_only_logged(self):
        self._enforce(False)
        with self.assertLogs(ACCESS_LOGGER, level="WARNING") as logs:
            self.assertTrue(self.customer._pms_api_check_access())
            self.assertEqual(self._check_uid(self.customer), self.customer.id)
        self.assertIn("would be refused", logs.output[0])

    def test_hotel_users_api_clients_and_internal_users_log_in(self):
        self._enforce()
        for user in (self.receptionist, self.api_client, self.employee):
            with self.subTest(user=user.login):
                self.assertTrue(self._login(user).token)

    def test_a_token_stops_working_when_the_role_is_withdrawn(self):
        self._enforce()
        self.assertEqual(self._check_uid(self.receptionist), self.receptionist.id)
        self.receptionist.pms_api_user_role = False
        with self.assertRaises(UnauthorizedInvalidToken):
            self._check_uid(self.receptionist)

    def test_other_validators_are_left_alone(self):
        self._enforce()
        other = self.validator.copy({"name": "not_the_pms"})
        self.assertEqual(
            other._get_and_check_uid({"username": self.customer.login}),
            self.customer.id,
        )

    def test_a_role_without_a_value_is_not_returned_as_false(self):
        self._enforce(False)
        self.assertIsNone(self._login(self.employee).userRole)

    def test_the_migration_keeps_the_role_only_for_hotel_users(self):
        # Neither of these two works at a property, and both keep their role.
        api_client = self._create_user(
            "booking-engine@example.org",
            portal=True,
            at_property=False,
            pms_api_client=True,
        )
        employee = self._create_user("back-office@example.org", at_property=False)
        users = self.customer | self.receptionist | api_client | employee
        users.write({"pms_api_user_role": "receptionist"})
        # The migration is plain SQL: it only sees what has reached the table.
        self.env.flush_all()
        self._load_migration().clear_roles_of_customers(self.env.cr)
        users.invalidate_recordset(["pms_api_user_role"])
        self.assertFalse(self.customer.pms_api_user_role)
        for user in self.receptionist | api_client | employee:
            with self.subTest(user=user.login):
                self.assertEqual(user.pms_api_user_role, "receptionist")

    def _load_migration(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "migrations",
            "16.0.1.7.0",
            "post-migration.py",
        )
        spec = importlib.util.spec_from_file_location("hotel_users_migration", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        return migration
