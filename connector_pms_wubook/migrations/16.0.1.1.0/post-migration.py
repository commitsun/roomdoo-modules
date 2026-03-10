from openupgradelib import openupgrade


@openupgrade.migrate()
def migrate(env, version):
    # Populate new payment method line fields from existing journal columns.
    # The journal columns (wubook_journal_id, journal_id) are kept as-is.
    # We resolve each journal to its inbound "manual" payment method line.
    manual_method_id = env.ref("account.account_payment_method_manual_in").id

    env.cr.execute(
        """
        UPDATE channel_wubook_backend cwb
        SET wubook_payment_method_line_id = apml.id
        FROM account_payment_method_line apml
        WHERE apml.journal_id = cwb.wubook_journal_id
          AND apml.payment_method_id = %s
        """,
        (manual_method_id,),
    )

    env.cr.execute(
        """
        UPDATE wubook_backend_journal_ota wbjo
        SET payment_method_line_id = apml.id
        FROM account_payment_method_line apml
        WHERE apml.journal_id = wbjo.journal_id
          AND apml.payment_method_id = %s
        """,
        (manual_method_id,),
    )
