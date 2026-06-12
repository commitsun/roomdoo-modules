from openupgradelib import openupgrade


@openupgrade.migrate()
def migrate(env, version):
    # The archive cascade in res.partner.write never ran (broken guard), so
    # archived partners kept active identification documents that blocked the
    # uniqueness checks for new contacts/check-ins reusing the same number.
    # Archive those orphaned documents.
    openupgrade.logged_query(
        env.cr,
        """
        UPDATE res_partner_id_number n
        SET active = false
        FROM res_partner p
        WHERE n.partner_id = p.id
          AND p.active = false
          AND n.active = true
        """,
    )
