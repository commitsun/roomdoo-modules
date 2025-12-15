def post_init_hook(cr, registry):
    from odoo import SUPERUSER_ID, api

    env = api.Environment(cr, SUPERUSER_ID, {})
    base_url = env["ir.config_parameter"].sudo().get_param("web.base.url")

    # set the roomdoo app menu base url
    env.ref("pms_api_rest_dashboard_url.default_report_url").write(
        {"base_url": base_url + "/login_portal_token"}
    )
