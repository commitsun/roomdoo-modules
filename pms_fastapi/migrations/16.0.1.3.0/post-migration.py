"""Mark as ``allowed_on_pms = TRUE`` any journal that is either:

* explicitly restricted to one or more ``pms_property_ids``, or
* already used in real ``account.move`` records that have ``pms_property_id``
  set (i.e. PMS-context invoices, including those on "generic" journals
  without ``pms_property_ids``).

Journals never used and never configured for PMS stay ``FALSE`` and require
manual review — that is the ambiguity the flag was introduced to surface.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute(
        """
        UPDATE account_journal
        SET allowed_on_pms = TRUE
        WHERE id IN (
            SELECT account_journal_id FROM account_journal_pms_property_rel
            UNION
            SELECT DISTINCT journal_id
            FROM account_move
            WHERE pms_property_id IS NOT NULL
        )
        """
    )
    if cr.rowcount:
        _logger.info(
            "Marked %d journals as allowed_on_pms (config + historical use).",
            cr.rowcount,
        )
