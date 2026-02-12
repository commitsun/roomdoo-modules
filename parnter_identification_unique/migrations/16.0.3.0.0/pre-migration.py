from openupgradelib import openupgrade


@openupgrade.migrate()
def migrate(env, version):
    openupgrade.logged_query(
        env.cr,
        "DELETE FROM ir_model_data WHERE module = 'parnter_identification_unique'",
    )
