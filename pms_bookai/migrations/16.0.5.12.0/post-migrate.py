import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    # BookAI keeps the folios of the templates it accepted.
    cr.execute(
        """
        UPDATE pms_folio folio
           SET bookai_known = TRUE
          FROM pms_notification_log log
         WHERE log.bookai_origin_folio_id = folio.id
           AND log.bookai_last_http_status BETWEEN 200 AND 299
        """
    )
    _logger.info("pms_bookai 16.0.5.12.0: %s folios known by BookAI", cr.rowcount)

    # Send the stay status of the current ones on the next cron runs; older
    # stays keep no status in BookAI.
    cr.execute(
        """
        UPDATE pms_folio
           SET bookai_push_pending = TRUE
         WHERE bookai_known
           AND (last_checkout IS NULL OR last_checkout >= CURRENT_DATE - 30)
        """
    )
    _logger.info(
        "pms_bookai 16.0.5.12.0: %s folios queued for a BookAI push", cr.rowcount
    )
