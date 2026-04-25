import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    _logger.info(
        "pms_bookai 16.0.4.0.0 post-migrate: "
        "creating WA accounts and phones from legacy property data"
    )

    # Check if legacy columns exist
    cr.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'pms_property'
          AND column_name = '_legacy_wa_account_id'
        """
    )
    if not cr.fetchone():
        _logger.info("  No legacy WA columns found, skipping migration")
        return

    # 1. Collect distinct WABAs from properties
    cr.execute(
        """
        SELECT DISTINCT _legacy_wa_account_id,
               _legacy_wa_access_token,
               _legacy_wa_verify_token
        FROM pms_property
        WHERE _legacy_wa_account_id IS NOT NULL
          AND _legacy_wa_account_id != ''
        """
    )
    waba_rows = cr.fetchall()
    _logger.info("  Found %d distinct WABAs", len(waba_rows))

    waba_map = {}  # waba_id_str → new bookai_wa_account.id
    for waba_id_str, access_token, verify_token in waba_rows:
        cr.execute(
            """
            INSERT INTO bookai_wa_account (
                name, waba_id, access_token, verify_token,
                active, create_date, write_date, create_uid, write_uid
            ) VALUES (
                %s, %s, %s, %s,
                true, now(), now(), 1, 1
            )
            ON CONFLICT (waba_id) DO UPDATE SET
                access_token = EXCLUDED.access_token,
                verify_token = EXCLUDED.verify_token
            RETURNING id
            """,
            (
                f"WABA {waba_id_str}",
                waba_id_str,
                access_token or None,
                verify_token or None,
            ),
        )
        wa_account_id = cr.fetchone()[0]
        waba_map[waba_id_str] = wa_account_id
        _logger.info(
            "  Created/updated wa.account id=%d for WABA %s",
            wa_account_id,
            waba_id_str,
        )

    # 2. Create phones and link properties
    cr.execute(
        """
        SELECT id, _legacy_wa_account_id,
               _legacy_wa_phone_number_id,
               _legacy_wa_display_number
        FROM pms_property
        WHERE _legacy_wa_phone_number_id IS NOT NULL
          AND _legacy_wa_phone_number_id != ''
        """
    )
    prop_rows = cr.fetchall()
    _logger.info("  Found %d properties with WA phone", len(prop_rows))

    for prop_id, waba_id_str, phone_number_id, display_number in prop_rows:
        wa_account_id = waba_map.get(waba_id_str)
        if not wa_account_id:
            _logger.warning(
                "  Property %d has phone but no WABA, skipping",
                prop_id,
            )
            continue

        # Create or get phone
        cr.execute(
            """
            SELECT id FROM bookai_wa_phone
            WHERE phone_number_id = %s
            """,
            (phone_number_id,),
        )
        phone_row = cr.fetchone()
        if phone_row:
            wa_phone_id = phone_row[0]
        else:
            cr.execute(
                """
                INSERT INTO bookai_wa_phone (
                    name, wa_account_id, phone_number_id,
                    display_number, active,
                    create_date, write_date, create_uid, write_uid
                ) VALUES (
                    %s, %s, %s, %s, true,
                    now(), now(), 1, 1
                )
                RETURNING id
                """,
                (
                    display_number or phone_number_id,
                    wa_account_id,
                    phone_number_id,
                    display_number or None,
                ),
            )
            wa_phone_id = cr.fetchone()[0]

        # Link property → phone
        cr.execute(
            """
            UPDATE pms_property
            SET bookai_wa_phone_id = %s
            WHERE id = %s
            """,
            (wa_phone_id, prop_id),
        )
        _logger.info(
            "  Property %d → phone %d (WABA %s)",
            prop_id,
            wa_phone_id,
            waba_id_str,
        )

    # 3. Duplicate translations per WABA if needed
    cr.execute(
        """
        SELECT t.id, t.template_id, t.language,
               t.meta_template_id, t.meta_status
        FROM bookai_whatsapp_translation t
        WHERE t.wa_account_id IS NULL
          AND t.active = true
        """
    )
    trans_rows = cr.fetchall()
    _logger.info("  Found %d translations without wa_account_id", len(trans_rows))

    for trans_id, template_id, language, _meta_tmpl_id, _meta_status in trans_rows:
        # Find WABAs for this template's properties
        cr.execute(
            """
            SELECT DISTINCT bwa.id
            FROM pms_notification_template_pms_property_rel rel
            JOIN pms_property p ON p.id = rel.pms_property_id
            JOIN bookai_wa_phone bwp ON bwp.id = p.bookai_wa_phone_id
            JOIN bookai_wa_account bwa ON bwa.id = bwp.wa_account_id
            WHERE rel.pms_notification_template_id = %s
            """,
            (template_id,),
        )
        wa_account_ids = [r[0] for r in cr.fetchall()]

        if not wa_account_ids:
            # No WA accounts for this template's properties, leave as is
            continue

        if len(wa_account_ids) == 1:
            # Single WABA: just update the existing translation
            cr.execute(
                """
                UPDATE bookai_whatsapp_translation
                SET wa_account_id = %s
                WHERE id = %s
                """,
                (wa_account_ids[0], trans_id),
            )
        else:
            # Multiple WABAs: update first, duplicate for the rest
            cr.execute(
                """
                UPDATE bookai_whatsapp_translation
                SET wa_account_id = %s
                WHERE id = %s
                """,
                (wa_account_ids[0], trans_id),
            )
            for wa_acc_id in wa_account_ids[1:]:
                cr.execute(
                    """
                    INSERT INTO bookai_whatsapp_translation (
                        template_id, wa_account_id, language,
                        meta_template_id, meta_status, active,
                        create_date, write_date, create_uid, write_uid
                    ) VALUES (
                        %s, %s, %s, NULL, 'draft', true,
                        now(), now(), 1, 1
                    )
                    ON CONFLICT (template_id, language, wa_account_id)
                    DO NOTHING
                    """,
                    (template_id, wa_acc_id, language),
                )

    # 4. Drop legacy columns
    for _, legacy_name in [
        ("bookai_wa_account_id", "_legacy_wa_account_id"),
        ("bookai_wa_access_token", "_legacy_wa_access_token"),
        ("bookai_wa_verify_token", "_legacy_wa_verify_token"),
        ("bookai_wa_phone_number_id", "_legacy_wa_phone_number_id"),
        ("bookai_wa_display_number", "_legacy_wa_display_number"),
    ]:
        cr.execute(f"ALTER TABLE pms_property DROP COLUMN IF EXISTS {legacy_name}")

    _logger.info("  Migration complete")
