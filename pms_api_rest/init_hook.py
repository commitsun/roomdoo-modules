def post_init_hook(cr, registry):
    from odoo import SUPERUSER_ID, api

    env = api.Environment(cr, SUPERUSER_ID, {})
    base_url = env["ir.config_parameter"].sudo().get_param("web.base.url")

    import urllib.parse

    netloc = urllib.parse.urlparse(base_url).netloc
    first_subdomain = netloc.split(".")[0] if netloc else ""

    if first_subdomain:
        app_url = f"https://{first_subdomain}.roomdoo.com"
        env["ir.config_parameter"].sudo().set_param("roomdoo_app_url", app_url)

    # set the roomdoo app menu base url
    env.ref("pms_api_rest.default_report_url").write(
        {"base_url": base_url + "/login_portal_token"}
    )
