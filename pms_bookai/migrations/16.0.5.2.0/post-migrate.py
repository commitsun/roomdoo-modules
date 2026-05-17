import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    _logger.info(
        "pms_bookai 16.0.5.2.0 post-migrate: "
        "moving allowed_agent_ids M2M into agent.delegation records"
    )

    cr.execute(
        """
        SELECT to_regclass('public.bookai_agent_allowed_agents_rel') IS NOT NULL
        """
    )
    has_legacy_table = cr.fetchone()[0]
    if not has_legacy_table:
        _logger.info("  Legacy M2M table not found, skipping")
        return

    cr.execute(
        """
        INSERT INTO bookai_agent_delegation (
            agent_id, delegate_agent_id, active,
            create_date, write_date, create_uid, write_uid
        )
        SELECT rel.agent_id, rel.allowed_agent_id, true,
               now(), now(), 1, 1
        FROM bookai_agent_allowed_agents_rel rel
        WHERE rel.agent_id <> rel.allowed_agent_id
          AND NOT EXISTS (
            SELECT 1 FROM bookai_agent_delegation d
            WHERE d.agent_id = rel.agent_id
              AND d.delegate_agent_id = rel.allowed_agent_id
          )
        """
    )
    migrated = cr.rowcount
    _logger.info("  Created %d delegations from legacy M2M", migrated)

    cr.execute("DROP TABLE IF EXISTS bookai_agent_allowed_agents_rel")
    _logger.info("  Dropped legacy table bookai_agent_allowed_agents_rel")
