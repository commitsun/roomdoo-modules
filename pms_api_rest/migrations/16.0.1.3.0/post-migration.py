from openupgradelib import openupgrade


@openupgrade.migrate()
def migrate(env, version):
    base_url = env["ir.config_parameter"].sudo().get_param("web.base.url")
    env.ref("pms_api_rest.default_report_url").write(
        {"base_url": base_url + "/login_portal_token"}
    )
