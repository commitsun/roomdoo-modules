import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Migrate bookai.agent.tool → bookai.tool + bindings.

    Runs before ORM creates new tables, so we just rename the
    old table and clean up references. The ORM will create
    the new tables, then post-migrate populates them.
    """
    if not version:
        return

    cr.execute(
        "SELECT EXISTS ("
        "  SELECT FROM information_schema.tables "
        "  WHERE table_name = 'bookai_agent_tool'"
        ")"
    )
    if not cr.fetchone()[0]:
        return

    _logger.info(
        "Pre-migrate: renaming bookai_agent_tool → "
        "bookai_agent_tool_old for migration."
    )
    cr.execute("ALTER TABLE bookai_agent_tool " "RENAME TO bookai_agent_tool_old")

    # Clean up ORM references to the old model
    cr.execute(
        "DELETE FROM ir_model_data "
        "WHERE model = 'bookai.agent.tool' "
        "AND module = 'pms_bookai'"
    )
    cr.execute(
        "DELETE FROM ir_model_access "
        "WHERE model_id IN ("
        "  SELECT id FROM ir_model "
        "  WHERE model = 'bookai.agent.tool'"
        ")"
    )
    cr.execute(
        "DELETE FROM ir_model_data "
        "WHERE model = 'ir.model' "
        "AND name = 'model_bookai_agent_tool'"
    )
    cr.execute(
        "DELETE FROM ir_model_fields "
        "WHERE model_id IN ("
        "  SELECT id FROM ir_model "
        "  WHERE model = 'bookai.agent.tool'"
        ")"
    )
    cr.execute("DELETE FROM ir_model " "WHERE model = 'bookai.agent.tool'")
    _logger.info("Pre-migrate complete.")
