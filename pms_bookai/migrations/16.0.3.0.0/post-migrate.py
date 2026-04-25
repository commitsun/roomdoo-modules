import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Populate bookai.tool + bindings from old data."""
    if not version:
        return

    cr.execute(
        "SELECT EXISTS ("
        "  SELECT FROM information_schema.tables "
        "  WHERE table_name = 'bookai_agent_tool_old'"
        ")"
    )
    if not cr.fetchone()[0]:
        return

    _logger.info(
        "Post-migrate: populating bookai_tool + bindings " "from bookai_agent_tool_old."
    )

    # Create global tool entries only if not already created
    # by data XML (avoid duplicate key on name+tool_type)
    cr.execute(
        """
        INSERT INTO bookai_tool
            (name, description, tool_type, sdk_method,
             requires_confirm, active,
             create_uid, create_date, write_uid, write_date)
        SELECT DISTINCT ON (old.name)
            old.name,
            COALESCE(old.description, old.name),
            'sdk',
            old.name,
            old.requires_confirm,
            old.active,
            1, NOW() AT TIME ZONE 'UTC',
            1, NOW() AT TIME ZONE 'UTC'
        FROM bookai_agent_tool_old old
        WHERE NOT EXISTS (
            SELECT 1 FROM bookai_tool t
            WHERE t.name = old.name AND t.tool_type = 'sdk'
        )
        ORDER BY old.name, old.id
        """
    )
    _logger.info("Created bookai_tool records (skipped existing).")

    # Create bindings only if not already created by data XML
    cr.execute(
        """
        INSERT INTO bookai_agent_tool_binding
            (agent_id, tool_id, requires_confirm, active,
             create_uid, create_date, write_uid, write_date)
        SELECT
            old.agent_id,
            new.id,
            old.requires_confirm,
            old.active,
            1, NOW() AT TIME ZONE 'UTC',
            1, NOW() AT TIME ZONE 'UTC'
        FROM bookai_agent_tool_old old
        JOIN bookai_tool new
            ON new.name = old.name AND new.tool_type = 'sdk'
        WHERE NOT EXISTS (
            SELECT 1 FROM bookai_agent_tool_binding b
            WHERE b.agent_id = old.agent_id
              AND b.tool_id = new.id
        )
        """
    )
    _logger.info("Created bindings (skipped existing).")

    # Drop old table
    cr.execute("DROP TABLE IF EXISTS bookai_agent_tool_old")
    _logger.info("Post-migrate complete.")
