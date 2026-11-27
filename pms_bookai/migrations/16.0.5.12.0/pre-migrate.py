from odoo.addons.pms_bookai import add_folio_push_columns


def migrate(cr, version):
    if not version:
        return
    add_folio_push_columns(cr)
