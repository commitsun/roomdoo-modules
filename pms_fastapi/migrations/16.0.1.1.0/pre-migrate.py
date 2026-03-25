import logging

from odoo.tools.sql import column_exists

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    if column_exists(cr, "pms_folio", "fastapi_sort_state"):
        return

    _logger.info("Adding fastapi_sort_state column to pms_folio with default '3_other'")
    cr.execute(
        """
        ALTER TABLE pms_folio
        ADD COLUMN fastapi_sort_state VARCHAR DEFAULT '3_other'
    """
    )
