import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    _logger.info("Computing fastapi_sort_state for pms_folio")
    cr.execute(
        """
        UPDATE pms_folio f
        SET fastapi_sort_state = sub.new_fastapi_sort_state
        FROM (
            SELECT f2.id,
                COALESCE(
                    CASE MIN(
                        CASE r.state
                            WHEN 'arrival_delayed' THEN 1
                            WHEN 'confirm' THEN 2
                            WHEN 'onboard' THEN 3
                            WHEN 'departure_delayed' THEN 4
                            WHEN 'draft' THEN 5
                            WHEN 'cancel' THEN 6
                            WHEN 'done' THEN 7
                            ELSE 8
                        END
                    )
                        WHEN 1 THEN '0_arriving'
                        WHEN 2 THEN '0_arriving'
                        WHEN 3 THEN '1_onboard'
                        WHEN 4 THEN '1_onboard'
                        WHEN 5 THEN '3_other'
                        WHEN 6 THEN '3_other'
                        WHEN 7 THEN '2_departed'
                        ELSE '3_other'
                    END,
                    '3_other'
                ) AS new_fastapi_sort_state
            FROM pms_folio f2
            LEFT JOIN pms_reservation r
                ON r.folio_id = f2.id
                AND (r.cancelled_reason IS NULL OR r.cancelled_reason != 'modified')
            GROUP BY f2.id
        ) sub
        WHERE f.id = sub.id
        AND f.fastapi_sort_state IS DISTINCT FROM sub.new_fastapi_sort_state
    """
    )
    _logger.info("fastapi_sort_state computed: %d folios updated", cr.rowcount)
