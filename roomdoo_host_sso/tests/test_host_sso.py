import time
import uuid
from datetime import timedelta
from unittest import mock

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

import odoo
from odoo import fields
from odoo.exceptions import AccessDenied
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged

from odoo.addons.roomdoo_host_link.models.host_link import PARAM_INSTANCE, PARAM_URL

HOST = "https://host.example.com"
GET_KEY = "odoo.addons.auth_jwt.models.auth_jwt_validator.AuthJwtValidator._get_key"
HOST_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
WHO = "agent@roomdoo.example"


class HostTokens:
    """Host's side of the handshake: its key, and tokens signed with it."""

    @staticmethod
    def _link(env):
        icp = env["ir.config_parameter"].sudo()
        icp.set_param(PARAM_URL, HOST)
        icp.set_param(PARAM_INSTANCE, "hotelone")

    @staticmethod
    def _token(door="back", key=HOST_KEY, **claims):
        now = int(time.time())
        payload = {
            "iss": HOST,
            "aud": f"hotelone-{door}",
            "sub": WHO,
            "email": WHO,
            "name": "Agent",
            "profile": "full",
            "until": now + 4 * 3600,
            "iat": now,
            "exp": now + 60,
            "jti": uuid.uuid4().hex,
        }
        payload.update(claims)
        return jwt.encode(payload, key, algorithm="RS256", headers={"kid": "k1"})


@tagged("post_install", "-at_install")
class TestHostSso(HostTokens, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._link(cls.env)
        cls.sso = cls.env["roomdoo.host.sso"]
        patcher = mock.patch(GET_KEY, return_value=HOST_KEY.public_key())
        patcher.start()
        cls.addClassCleanup(patcher.stop)

    def test_the_team_comes_in_with_a_user_of_their_own(self):
        user = self.sso._enter(self._token(), "back")
        self.assertEqual(user.login, WHO)
        self.assertTrue(user.host_sso_user)
        self.assertTrue(user.active)
        profile = self.env.ref("roomdoo_host_sso.profile_full")
        self.assertTrue(profile.group_ids <= user.groups_id)
        self.assertEqual(user.pms_property_ids, self.env["pms.property"].search([]))
        self.assertEqual(user.company_ids, self.env["res.company"].search([]))
        self.assertAlmostEqual(
            user.host_sso_until,
            fields.Datetime.now() + timedelta(hours=4),
            delta=timedelta(minutes=1),
        )

    def test_the_validator_follows_the_host_link(self):
        self.sso._enter(self._token(), "back")
        validator = self.env["auth.jwt.validator"].search(
            [("name", "=", "roomdoo_host")]
        )
        self.assertEqual(validator.issuer, HOST)
        self.assertEqual(validator.public_key_jwk_uri, HOST + "/roomdoo/sso/jwks.json")
        self.assertEqual(validator.audience, "hotelone-back,hotelone-front")

    def test_a_token_opens_its_door_once(self):
        token = self._token()
        self.sso._enter(token, "back")
        with self.assertRaises(AccessDenied):
            self.sso._enter(token, "back")

    def test_a_token_opens_only_its_door_of_this_instance(self):
        refused = [
            (self._token("front"), "back"),
            (self._token(aud="otherhotel-back"), "back"),
            (self._token(iss="https://elsewhere.example.com"), "back"),
            (self._token(exp=int(time.time()) - 5), "back"),
            (self._token(jti=""), "back"),
        ]
        for token, door in refused:
            with self.assertRaises(AccessDenied):
                self.sso._enter(token, door)

    def test_only_host_signs(self):
        stranger = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with self.assertRaises(AccessDenied):
            self.sso._enter(self._token(key=stranger), "back")

    def test_the_next_entry_reactivates_and_never_cuts_short(self):
        later = int(time.time()) + 8 * 3600
        user = self.sso._enter(self._token(until=later), "back")
        long_until = user.host_sso_until
        self.sso._enter(self._token(), "back")
        self.assertEqual(user.host_sso_until, long_until)
        user.active = False
        self.sso._enter(self._token(), "back")
        self.assertTrue(user.active)
        self.assertLess(user.host_sso_until, long_until)

    def test_nobody_stays_more_than_a_day(self):
        user = self.sso._enter(self._token(until=int(time.time()) + 72 * 3600), "back")
        self.assertLessEqual(
            user.host_sso_until,
            fields.Datetime.now() + timedelta(hours=24, minutes=1),
        )

    def test_a_user_of_the_instance_is_never_taken_over(self):
        new_test_user(self.env, login=WHO, groups="base.group_user")
        with self.assertRaises(AccessDenied):
            self.sso._enter(self._token(), "back")

    def test_an_unknown_profile_lets_nobody_in(self):
        with self.assertRaises(AccessDenied):
            self.sso._enter(self._token(profile="nobody"), "back")

    def test_an_instance_not_linked_lets_nobody_in(self):
        self.env["ir.config_parameter"].sudo().set_param(PARAM_INSTANCE, "")
        with self.assertRaises(AccessDenied):
            self.sso._enter(self._token(), "back")

    def test_the_team_never_logs_in_any_other_way(self):
        user = self.sso._enter(self._token(), "back")
        with self.assertRaises(AccessDenied):
            user.with_user(user)._check_credentials("anything", {"interactive": True})
        user.partner_id.signup_prepare()
        self.assertTrue(user.partner_id.signup_token)
        self.assertFalse(user.partner_id.signup_valid)

    def test_the_cron_archives_whoever_is_done(self):
        done = self.sso._enter(self._token(), "back")
        stays = self.sso._enter(self._token(sub="other@roomdoo.example"), "back")
        done.host_sso_until = fields.Datetime.now() - timedelta(minutes=1)
        self.env["res.users"]._cron_archive_host_access()
        self.assertFalse(done.active)
        self.assertTrue(stays.active)


@tagged("post_install", "-at_install")
class TestHostSsoDoors(HostTokens, HttpCase):
    def setUp(self):
        super().setUp()
        self._link(self.env)
        self.env["ir.config_parameter"].sudo().set_param(
            "roomdoo_app_url", "https://app.example.com"
        )
        patcher = mock.patch(GET_KEY, return_value=HOST_KEY.public_key())
        patcher.start()
        self.addCleanup(patcher.stop)

    def _open(self, door, token):
        return self.url_open(
            f"/roomdoo/sso/{door}?token={token}", allow_redirects=False
        )

    def test_the_back_door_opens_a_session(self):
        response = self._open("back", self._token())
        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.headers["Location"].endswith("/web"))
        session = odoo.http.root.session_store.get(self.opener.cookies["session_id"])
        user = self.env["res.users"].search([("login", "=", WHO)])
        self.assertEqual(session.uid, user.id)

    def test_the_app_door_hands_over_the_app_cookies(self):
        response = self._open("front", self._token("front"))
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["Location"], "https://app.example.com")
        cookies = [
            value.split("=", 1)[0]
            for value in response.raw.headers.getlist("Set-Cookie")
        ]
        self.assertIn("authorization", cookies)
        self.assertIn("refresh", cookies)

    def test_a_used_link_is_refused(self):
        token = self._token()
        self._open("back", token)
        self.opener.cookies.clear()
        response = self._open("back", token)
        self.assertEqual(response.status_code, 403)
