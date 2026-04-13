def pre_init_hook(cr):
    # autoinvoice_date was moved here from pms. The pms migration renamed the
    # column to autoinvoice_date_moved to preserve the data. Rename it back so
    # Odoo finds the column already populated and skips the expensive recompute.
    cr.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'folio_sale_line'
                  AND column_name = 'autoinvoice_date_moved'
                  AND table_schema = 'public'
            ) THEN
                ALTER TABLE folio_sale_line
                    RENAME COLUMN autoinvoice_date_moved TO autoinvoice_date;
            END IF;
        END $$;
    """)
