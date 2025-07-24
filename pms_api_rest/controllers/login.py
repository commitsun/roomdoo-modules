
import json
from odoo import http, _, SUPERUSER_ID
from odoo.exceptions import AccessDenied
from odoo.http import request


class LoginPortalToken(http.Controller):

    @http.route(
        ["/login_portal_token"],
        type="http",
        auth="public",
        methods=["GET"],
    )
    def login_portal_token(self, login, token, redirect_url=None, **kwargs):
        """
        Login a user to the portal using a token.
        :param user_id: ID of the user to log in.
        :param token: Token for authentication.
        :param redirect_url: Optional URL to redirect after login.
        """
        request.params['login'] = login
        request.params['password'] = token
        request.params['login_success'] = False
        if not request.uid:
            request.uid = SUPERUSER_ID

        values = request.params.copy()
        try:
            values['databases'] = http.db_list()
        except AccessDenied:
            values['databases'] = None

        old_uid = request.uid
        try:
            request.session.authenticate(request.session.db, request.params['login'], request.params['password'])
            request.params['login_success'] = True
            return request.redirect_query(redirect_url or '/web', query=kwargs)
        except AccessDenied:
            request.uid = old_uid
        return request.redirect('/')
