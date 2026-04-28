# Columns moved here from pms. The pms migration renamed them with a
# _moved suffix to prevent _process_end() from dropping them. Rename
# them back so Odoo finds the columns already populated and skips
# expensive recomputes / data loss.
_COLUMNS_TO_RESTORE = {
    "folio_sale_line": [("autoinvoice_date_moved", "autoinvoice_date")],
    "pms_property": [
        ("default_invoicing_policy_moved", "default_invoicing_policy"),
        ("invoicing_month_day_moved", "invoicing_month_day"),
        ("margin_days_autoinvoice_moved", "margin_days_autoinvoice"),
    ],
    "res_partner": [
        ("invoicing_policy_moved", "invoicing_policy"),
        ("invoicing_month_day_moved", "invoicing_month_day"),
        ("margin_days_autoinvoice_moved", "margin_days_autoinvoice"),
    ],
    "res_company": [
        ("pms_invoice_downpayment_policy_moved", "pms_invoice_downpayment_policy"),
    ],
    "account_journal": [
        ("avoid_autoinvoice_downpayment_moved", "avoid_autoinvoice_downpayment"),
    ],
}


def pre_init_hook(cr):
    for table, columns in _COLUMNS_TO_RESTORE.items():
        for old_name, new_name in columns:
            cr.execute(
                f"""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = '{table}'
                          AND column_name = '{old_name}'
                          AND table_schema = 'public'
                    ) THEN
                        ALTER TABLE "{table}"
                            RENAME COLUMN "{old_name}" TO "{new_name}";
                    END IF;
                END $$;
                """
            )
