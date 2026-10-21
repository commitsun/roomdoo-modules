from datetime import timedelta

from fastapi import status

from odoo import fields

from odoo.addons.pms_fastapi.tests.common import CommonTestPmsApi

from ..models.res_users import REFRESH_TOKEN_REUSE_INTERVAL


class TestRefreshToken(CommonTestPmsApi):
    def _refresh(self, test_client, token):
        # The cookie is scoped to the real route path, which the test client
        # does not use, so it is handed over explicitly.
        return test_client.post(
            "/refresh-token", headers={"Cookie": f"refresh={token}"}
        )

    def _token_record(self, token):
        return self.env["fastapi.user.refresh.token"].search([("token", "=", token)])

    def _login_token(self, test_client):
        self._login(test_client)
        return self.test_user.fastapi_refresh_token_ids.token

    def test_a_token_just_exchanged_still_renews_the_session(self):
        with self._create_test_client() as test_client:
            token = self._login_token(test_client)

            first = self._refresh(test_client, token)
            second = self._refresh(test_client, token)

            self.assertEqual(first.status_code, status.HTTP_204_NO_CONTENT, first.text)
            self.assertEqual(
                second.status_code, status.HTTP_204_NO_CONTENT, second.text
            )

    def test_an_exchanged_token_only_lives_on_for_the_reuse_interval(self):
        with self._create_test_client() as test_client:
            token = self._login_token(test_client)

            self._refresh(test_client, token)

            limit = fields.Datetime.now() + timedelta(
                seconds=REFRESH_TOKEN_REUSE_INTERVAL
            )
            self.assertLessEqual(self._token_record(token).expire, limit)

    def test_presenting_it_again_does_not_stretch_its_life(self):
        with self._create_test_client() as test_client:
            token = self._login_token(test_client)
            self._refresh(test_client, token)
            record = self._token_record(token)
            record.expire = fields.Datetime.now() + timedelta(seconds=5)
            expire = record.expire

            self._refresh(test_client, token)

            self.assertEqual(self._token_record(token).expire, expire)

    def test_once_the_interval_is_over_it_no_longer_renews(self):
        with self._create_test_client() as test_client:
            token = self._login_token(test_client)
            self._refresh(test_client, token)
            self._token_record(token).expire = fields.Datetime.now() - timedelta(
                seconds=1
            )

            response = self._refresh(test_client, token)

            self.assertEqual(
                response.status_code, status.HTTP_401_UNAUTHORIZED, response.text
            )
