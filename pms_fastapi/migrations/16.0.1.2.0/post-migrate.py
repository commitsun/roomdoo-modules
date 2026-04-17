"""Fix orphaned account.payment.method.line records.

When ``type`` or ``currency_id`` is changed on an ``account.journal``, the
stored compute ``_compute_inbound_payment_method_line_ids`` runs
``Command.clear()`` + ``Command.create()``.  Lines referenced by existing
payments cannot be deleted (FK constraint), so they end up with
``journal_id = NULL`` — orphaned but still pointed to by payments.

This migration reassigns those payments to the correct (current) payment
method line for the same journal + payment method, then deletes the orphans
that are no longer referenced.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    _reassign_payments(cr)
    _delete_orphan_lines(cr)


def _reassign_payments(cr):
    """Point payments from orphan lines to the current active line that
    shares the same journal (via the payment's move) and payment method."""
    cr.execute(
        """
        UPDATE account_payment ap
        SET payment_method_line_id = target.id
        FROM account_move am,
             account_payment_method_line orphan,
             account_payment_method_line target
        WHERE am.id = ap.move_id
          AND orphan.id = ap.payment_method_line_id
          AND orphan.journal_id IS NULL
          AND target.journal_id = am.journal_id
          AND target.payment_method_id = orphan.payment_method_id
          AND target.id != orphan.id
        """
    )
    count = cr.rowcount
    if count:
        _logger.info("Reassigned %d payments from orphan payment method lines.", count)


def _delete_orphan_lines(cr):
    """Delete orphan lines that no longer have any payment referencing them."""
    cr.execute(
        """
        DELETE FROM account_payment_method_line orphan
        WHERE orphan.journal_id IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM account_payment ap
              WHERE ap.payment_method_line_id = orphan.id
          )
        """
    )
    count = cr.rowcount
    if count:
        _logger.info("Deleted %d orphan payment method lines.", count)

    # Log remaining orphans (still referenced by payments without a matching
    # target line — these need manual review).
    cr.execute(
        "SELECT COUNT(*) FROM account_payment_method_line WHERE journal_id IS NULL"
    )
    remaining = cr.fetchone()[0]
    if remaining:
        _logger.warning(
            "%d orphan payment method lines remain (payments could not be "
            "reassigned — no matching active line found). These need manual review.",
            remaining,
        )
