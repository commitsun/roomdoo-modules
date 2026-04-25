import logging

_logger = logging.getLogger(__name__)

_LEGACY_COLUMNS = [
    ("bookai_wa_account_id", "_legacy_wa_account_id"),
    ("bookai_wa_access_token", "_legacy_wa_access_token"),
    ("bookai_wa_verify_token", "_legacy_wa_verify_token"),
    ("bookai_wa_phone_number_id", "_legacy_wa_phone_number_id"),
    ("bookai_wa_display_number", "_legacy_wa_display_number"),
]


def migrate(cr, version):
    if not version:
        return

    _logger.info(
        "pms_bookai 16.0.4.0.0 pre-migrate: "
        "renaming legacy WA columns on pms_property"
    )
    for old_name, new_name in _LEGACY_COLUMNS:
        cr.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'pms_property'
              AND column_name = %s
            """,
            (old_name,),
        )
        if cr.fetchone():
            cr.execute(
                f"ALTER TABLE pms_property " f"RENAME COLUMN {old_name} TO {new_name}"
            )
            _logger.info("  Renamed %s → %s", old_name, new_name)
        else:
            _logger.info("  Column %s not found, skipping", old_name)
