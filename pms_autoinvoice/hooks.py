def pre_init_hook(cr):
    # autoinvoice_date was moved here from pms. The pms migration renamed the
    # column to autoinvoice_date_moved to preserve the data. Rename it back so
    # Odoo finds the column already populated and skips the expensive recompute.
    cr.execute(
        """
        ALTER TABLE folio_sale_line
        RENAME COLUMN autoinvoice_date_moved TO autoinvoice_date
        """
    )
