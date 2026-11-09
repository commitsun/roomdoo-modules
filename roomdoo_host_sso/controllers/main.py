from odoo import _, http
from odoo.exceptions import AccessDenied
from odoo.http import request


class HostEntry(http.Controller):
    """The two doors host sends the Roomdoo team through."""

    def _refused(self, message):
        return request.make_response(
            message,
            headers=[("Content-Type", "text/plain; charset=utf-8")],
            status=403,
        )

    @http.route(
        "/roomdoo/sso/back", type="http", auth="public", methods=["GET"], csrf=False
    )
    def back(self, token="", **kwargs):
        try:
            user = request.env["roomdoo.host.sso"].sudo()._enter(token, "back")
        except AccessDenied as exc:
            return self._refused(str(exc))
        # As /login_portal_token does: host already answered for who this is.
        # Not logged as a login of the instance, which counts as the
        # customer's use of it: host records who came in.
        request.session.pre_login = user.login
        request.session.pre_uid = user.id
        request.session.finalize(request.env)
        request.update_env(user=request.session.uid)
        request.update_context(**request.session.context)
        return request.redirect("/web")

    @http.route(
        "/roomdoo/sso/front",
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
        save_session=False,
    )
    def front(self, token="", **kwargs):
        icp = request.env["ir.config_parameter"].sudo()
        app_url = icp.get_param("roomdoo_app_url")
        if not app_url:
            return self._refused(_("This instance has no app address."))
        try:
            user = request.env["roomdoo.host.sso"].sudo()._enter(token, "front")
        except AccessDenied as exc:
            return self._refused(str(exc))
        # The very cookies a login in the app sets, so that the app finds its
        # session as if the person had typed a password there.
        endpoint = request.env["pms.fastapi.login.endpoint"].sudo()
        session = endpoint._get_login_response_with_cookies(user)
        response = request.redirect(app_url, local=False)
        for name, value in session.raw_headers:
            if name.lower() == b"set-cookie":
                response.headers.add("Set-Cookie", value.decode("latin-1"))
        return response
