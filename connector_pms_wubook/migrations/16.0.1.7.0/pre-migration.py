# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import logging

from openupgradelib import openupgrade

_logger = logging.getLogger(__name__)


@openupgrade.migrate()
def migrate(env, version):
    # The bindings of board services point to the old board service model by
    # id, and the binding now points to product templates: the same ids would
    # bind each Wubook board to an unrelated product. Board services are not
    # migrated to products, so the bindings are dropped and made again, by
    # hand or when the board is imported.
    env.cr.execute(
        """
        DELETE FROM channel_wubook_pms_board_service
        RETURNING backend_id, external_id
        """
    )
    for backend_id, external_id in env.cr.fetchall():
        _logger.warning(
            "Wubook board '%s' of backend %s unbound from its old board "
            "service: bind it again to its board service product",
            external_id,
            backend_id,
        )
