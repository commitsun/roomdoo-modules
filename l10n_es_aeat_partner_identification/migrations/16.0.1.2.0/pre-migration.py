from openupgradelib import openupgrade


@openupgrade.migrate()
def migrate(env, version):
    if env["ir.model.data"].search(
        [
            ("module", "=", "l10n_es_aeat_partner_identification"),
            ("name", "=", "view_partner_id_category_form"),
        ],
        limit=1,
    ):
        env.ref(
            "l10n_es_aeat_partner_identification.view_partner_id_category_form"
        ).unlink()
