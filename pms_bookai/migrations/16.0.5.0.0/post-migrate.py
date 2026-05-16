import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    _logger.info(
        "pms_bookai 16.0.5.0.0 post-migrate: "
        "moving kb_document_ids M2M into agent.kb.binding records"
    )

    # Only migrate if the legacy M2M table still exists
    cr.execute(
        """
        SELECT to_regclass('public.bookai_agent_kb_document_rel') IS NOT NULL
        """
    )
    has_legacy_table = cr.fetchone()[0]
    if not has_legacy_table:
        _logger.info("  Legacy M2M table not found, skipping")
        return

    cr.execute(
        """
        INSERT INTO bookai_agent_kb_binding (
            agent_id, document_id, active,
            create_date, write_date, create_uid, write_uid
        )
        SELECT rel.agent_id, rel.document_id, true,
               now(), now(), 1, 1
        FROM bookai_agent_kb_document_rel rel
        WHERE NOT EXISTS (
            SELECT 1 FROM bookai_agent_kb_binding b
            WHERE b.agent_id = rel.agent_id
              AND b.document_id = rel.document_id
        )
        """
    )
    migrated = cr.rowcount
    _logger.info("  Created %d bindings from legacy M2M", migrated)

    cr.execute("DROP TABLE IF EXISTS bookai_agent_kb_document_rel")
    _logger.info("  Dropped legacy table bookai_agent_kb_document_rel")
