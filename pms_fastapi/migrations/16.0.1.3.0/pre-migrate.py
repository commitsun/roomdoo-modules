import logging

from odoo.tools.sql import column_exists

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    if not column_exists(cr, "res_partner", "identification_number"):
        return

    _logger.info(
        "Dropping orphan res_partner.identification_number column "
        "(field is now non-stored, search-only)."
    )
    cr.execute("ALTER TABLE res_partner DROP COLUMN identification_number")
