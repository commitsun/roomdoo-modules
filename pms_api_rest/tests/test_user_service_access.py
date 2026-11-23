# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""Access rules of the users service.

Every user reads, edits and changes the password of itself. Internal users
also read the other users, and only access rights managers edit them. The
password reset link always points at the front configured for this instance,
and no mail is sent when there is none.
"""

import time
from unittest.mock import patch

from jose import jwt

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import HttpCase, tagged
from odoo.tools import mute_logger

from odoo.addons.base_rest.controllers.main import _PseudoCollection
from odoo.addons.component.core import WorkContext
from odoo.addons.pms.tests.common import TestPms

SERVICE_MODULE = "odoo.addons.pms_api_rest.services.pms_user_service"
IMAGE_URL_BUILDER = f"{SERVICE_MODULE}.url_image_pms_api_rest"
FRONT_URL = "https://front.example.com"
REQUESTED_URL = "https://somewhere-else.example.com"
PORTAL_PASSWORD = "portal-secret"
RECEPTIONIST_PASSWORD = "receptionist-secret"
NEW_PASSWORD = "brand-new-secret"


@tagged("post_install", "-at_install")
class TestUserServiceAccess(TestPms):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        users = cls.env["res.users"].with_context(no_reset_password=True)
        cls.portal_user = users.create(
            {
                "name": "Signed Up",
                "login": "signed.up@example.org",
                "email": "signed.up@example.org",
                "password": PORTAL_PASSWORD,
                "groups_id": [Command.set([cls.env.ref("base.group_portal").id])],
            }
        )
        staff = {
            "company_id": cls.company1.id,
            "company_ids": [Command.set([cls.company1.id])],
            "pms_property_ids": [Command.set([cls.pms_property1.id])],
            "pms_property_id": cls.pms_property1.id,
        }
        cls.receptionist = users.create(
            dict(
                staff,
                name="Receptionist",
                login="receptionist@example.org",
                email="receptionist@example.org",
                password=RECEPTIONIST_PASSWORD,
            )
        )
        cls.access_manager = users.create(
            dict(
                staff,
                name="Access Manager",
                login="access.manager@example.org",
                email="access.manager@example.org",
                password="access-manager-secret",
                groups_id=[
                    Command.set(
                        [
                            cls.env.ref("base.group_user").id,
                            cls.env.ref("base.group_erp_manager").id,
                        ]
                    )
                ],
            )
        )

    def _users(self, caller):
        collection = _PseudoCollection("pms.services", self.env(user=caller))
        work = WorkContext(
            model_name="rest.service.registration", collection=collection
        )
        return work.component(usage="users")

    def _read(self, caller, user):
        # The image URL is built from the live HTTP request, which a unit test
        # has no way to provide.
        with patch(IMAGE_URL_BUILDER, return_value=""):
            return self._users(caller).get_user(user.id)

    def _edit(self, caller, user, email):
        profile = self.env.datamodels["pms.api.rest.user.output"](
            userId=user.id,
            userName=user.name,
            userEmail=email,
            userPhone=None,
            userImageBase64=None,
        )
        return self._users(caller).write_user(user.id, profile)

    def _change_password(self, caller, user, password):
        passwords = self.env.datamodels["pms.api.rest.user.input"](
            password=password, newPassword=NEW_PASSWORD
        )
        return self._users(caller).change_password(user.id, passwords)

    def _ask_for_a_reset(self, email):
        reset_request = self.env.datamodels["pms.api.rest.user.input"](
            userEmail=email, url=REQUESTED_URL
        )
        public_user = self.env.ref("base.public_user")
        with patch.object(
            type(self.env["mail.template"]), "send_mail", autospec=True
        ) as send_mail:
            sent = self._users(public_user).send_mail_to_reset_password(reset_request)
        return sent, send_mail

    def test_a_portal_account_cannot_read_someone_else(self):
        with self.assertRaises(AccessError):
            self._read(self.portal_user, self.receptionist)

    def test_a_portal_account_reads_itself(self):
        result = self._read(self.portal_user, self.portal_user)

        self.assertEqual(result.userEmail, "signed.up@example.org")

    def test_the_staff_keep_reading_their_colleagues(self):
        result = self._read(self.receptionist, self.access_manager)

        self.assertEqual(result.userEmail, "access.manager@example.org")

    def test_a_portal_account_cannot_edit_someone_else(self):
        with self.assertRaises(AccessError):
            self._edit(self.portal_user, self.receptionist, "taken@example.org")

        self.assertEqual(self.receptionist.email, "receptionist@example.org")

    def test_a_portal_account_edits_itself(self):
        self._edit(self.portal_user, self.portal_user, "moved@example.org")

        self.assertEqual(self.portal_user.email, "moved@example.org")

    def test_the_staff_cannot_edit_a_colleague(self):
        with self.assertRaises(AccessError):
            self._edit(self.receptionist, self.access_manager, "taken@example.org")

        self.assertEqual(self.access_manager.email, "access.manager@example.org")

    def test_an_access_rights_manager_edits_someone_else(self):
        self._edit(self.access_manager, self.receptionist, "front.desk@example.org")

        self.assertEqual(self.receptionist.email, "front.desk@example.org")

    def test_a_refused_caller_gets_the_same_answer_for_a_missing_user(self):
        """The rules are checked before the user is looked up."""
        last_user = (
            self.env["res.users"]
            .with_context(active_test=False)
            .search([], order="id desc", limit=1)
        )
        missing_id = last_user.id + 1000
        self.assertFalse(self.env["res.users"].browse(missing_id).exists())
        profile = self.env.datamodels["pms.api.rest.user.output"](
            userId=missing_id, userName="Nobody"
        )
        passwords = self.env.datamodels["pms.api.rest.user.input"](
            password="a-wrong-guess", newPassword=NEW_PASSWORD
        )
        calls = (
            ("get_user", self.portal_user, (missing_id,)),
            ("write_user", self.receptionist, (missing_id, profile)),
            ("change_password", self.portal_user, (missing_id, passwords)),
        )
        for method, caller, args in calls:
            with self.subTest(method=method, caller=caller.login):
                with self.assertRaises(AccessError):
                    getattr(self._users(caller), method)(*args)

    def test_nobody_changes_the_password_of_someone_else(self):
        """Not even an access rights manager, and the answer is the same
        whether the password sent is right or wrong."""
        for caller in (self.portal_user, self.access_manager):
            for password in (RECEPTIONIST_PASSWORD, "a-wrong-guess"):
                with self.subTest(caller=caller.login, password=password):
                    with self.assertRaises(AccessError):
                        self._change_password(caller, self.receptionist, password)

    def test_a_user_changes_its_own_password(self):
        self._change_password(self.portal_user, self.portal_user, PORTAL_PASSWORD)

        # Raises AccessDenied unless the new password is now the valid one
        self.portal_user.with_user(self.portal_user)._check_credentials(
            NEW_PASSWORD, {"interactive": True}
        )

    def test_the_reset_link_points_at_the_front_whatever_the_request_says(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "roomdoo_app_url", FRONT_URL + "/"
        )

        sent, send_mail = self._ask_for_a_reset("receptionist@example.org")

        self.assertTrue(sent)
        template = send_mail.call_args.args[0]
        body = template._render_field("body_html", self.receptionist.ids)[
            self.receptionist.id
        ]
        token = self.receptionist.partner_id.signup_token
        self.assertIn(f"{FRONT_URL}/reset-password?token={token}", body)
        self.assertNotIn(REQUESTED_URL, body)

    def test_without_a_usable_front_no_reset_mail_is_sent(self):
        """The backend has no page of its own to point the link at instead."""
        for app_url in (False, "False", "*", "front.example.com"):
            with self.subTest(app_url=app_url):
                self.env["ir.config_parameter"].sudo().set_param(
                    "roomdoo_app_url", app_url
                )

                with self.assertLogs(SERVICE_MODULE, level="WARNING") as logs:
                    sent, send_mail = self._ask_for_a_reset("receptionist@example.org")

                self.assertFalse(sent)
                send_mail.assert_not_called()
                self.assertFalse(self.receptionist.partner_id.signup_token)
                # The only trace operations get of the mails not being sent
                self.assertIn("roomdoo_app_url", logs.output[0])


@tagged("post_install", "-at_install")
class TestUserServiceAccessHttp(HttpCase):
    """Through the real routes: a refused caller gets a 403, as with any other
    lack of access rights, and the caller still reaches its own user."""

    def setUp(self):
        super().setUp()
        users = self.env["res.users"].with_context(no_reset_password=True)
        self.portal_user = users.create(
            {
                "name": "Signed Up",
                "login": "signed.up@example.org",
                "password": PORTAL_PASSWORD,
                "groups_id": [Command.set([self.env.ref("base.group_portal").id])],
            }
        )
        self.colleague = users.create(
            {"name": "Colleague", "login": "colleague@example.org"}
        )
        self.headers = {"Authorization": f"Bearer {self._token(self.portal_user)}"}

    def _token(self, user):
        validator = (
            self.env["auth.jwt.validator"].sudo()._get_validator_by_name("api_pms")
        )
        return jwt.encode(
            {
                "aud": "api_pms",
                "iss": "pms",
                "exp": int(time.time()) + 600,
                "username": user.login,
            },
            key=validator.secret_key,
            algorithm=validator.secret_algorithm,
        )

    def _call(self, method, path, payload=None):
        return self.opener.request(
            method,
            f"{self.base_url()}/api/users{path}",
            json=payload,
            headers=self.headers,
            timeout=12,
        )

    @mute_logger("odoo.addons.base_rest.http", "odoo.http")
    def test_a_portal_account_gets_a_403_for_someone_else(self):
        colleague = self.colleague
        calls = (
            ("GET", f"/{colleague.id}", None, "You can only view your own user."),
            (
                "PATCH",
                f"/p/{colleague.id}",
                {"userId": colleague.id, "userName": colleague.name},
                "You can only edit your own user.",
            ),
            (
                "PATCH",
                f"/p/{colleague.id}/change-password",
                {"password": "a-wrong-guess", "newPassword": NEW_PASSWORD},
                "You can only change your own password.",
            ),
        )
        for method, path, payload, message in calls:
            with self.subTest(method=method, path=path):
                response = self._call(method, path, payload)

                self.assertEqual(response.status_code, 403, response.text)
                # Other checks of this API answer 403 as well, with messages
                # of their own
                self.assertIn(message, response.json()["description"])

    def test_a_portal_account_reaches_its_own_user(self):
        """The id in the path is compared as the integer it is."""
        portal = self.portal_user

        response = self._call("GET", f"/{portal.id}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["userId"], portal.id)

        response = self._call(
            "PATCH", f"/p/{portal.id}", {"userId": portal.id, "userName": "Renamed"}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.env.invalidate_all()
        self.assertEqual(portal.name, "Renamed")

        response = self._call(
            "PATCH",
            f"/p/{portal.id}/change-password",
            {"password": PORTAL_PASSWORD, "newPassword": NEW_PASSWORD},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["login"], portal.login)
