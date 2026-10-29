from odoo import http
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
        Open a session for the owner of a single use token.
        :param login: Login of the user the link was handed to.
        :param token: Single use token standing in for the whole login.
        :param redirect_url: Optional URL to redirect after login.

        The token answers for both factors: the session that asked for the
        link had already answered for them, and the token is spent on the way
        in. So it is asked for the way `auth_totp` asks for a trusted device,
        by scope and by owner, and the session is finalized here. Handing it
        to the standard login instead would leave a protected account waiting
        for a code that this link has nobody to ask for, and would let a plain
        password through this door as well.
        """
        users = request.env["res.users"].sudo()
        user = users.search(
            users._get_login_domain(login), order=users._get_login_order(), limit=1
        )
        if not user or not request.env[
            "one.time.res.users.apikeys"
        ].sudo()._check_credentials_for_uid(scope="ots", key=token, uid=user.id):
            return request.redirect("/")

        request.session.pre_login = login
        request.session.pre_uid = user.id
        request.session.finalize(request.env)
        request.update_env(user=request.session.uid)
        request.update_context(**request.session.context)
        request.env.user._update_last_login()
        return request.redirect_query(redirect_url or "/web", query=kwargs)
