"""Backfill ``cash_session_closed`` for cash statements created before the
flag existed.

pms_fastapi tracks the open/closed state of a cash session explicitly on
``account.bank.statement.cash_session_closed``. Every statement created before
this field existed defaults to ``FALSE``, so without this backfill pms_fastapi
would read every historical cash session (including those created by the legacy
pms_api_rest API) as still open.

The flag was historically inferred from the standard accounting field
``is_complete`` (posted lines that balance ``balance_end`` against
``balance_end_real``); empty closes were deleted outright, so a complete cash
statement is unambiguously a closed session. We reuse that signal here. Closing
metadata (date/uid) is approximated from the statement's write metadata, the
same heuristic the legacy API used for ``date_done``.

Open sessions (``is_complete = FALSE``) keep ``cash_session_closed = FALSE``,
so the current shift per cash journal stays open.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    # (A) Mark completed cash statements as closed. Sessions created/closed by
    # the legacy API never set the flag; ``is_complete`` is the accounting
    # signal it used for "closed". Stamp the closing metadata from the write
    # metadata (same heuristic the legacy API used for ``date_done``).
    cr.execute(
        """
        UPDATE account_bank_statement st
        SET cash_session_closed = TRUE,
            cash_session_closed_date = COALESCE(
                st.cash_session_closed_date, st.write_date),
            cash_session_closed_uid = COALESCE(
                st.cash_session_closed_uid, st.write_uid)
        FROM account_journal j
        WHERE st.journal_id = j.id
          AND j.type = 'cash'
          AND st.is_complete = TRUE
          AND st.cash_session_closed IS NOT TRUE
        """
    )
    marked = cr.rowcount
    # (B) Repair already-closed sessions missing the closing metadata. Legacy
    # rows may have the flag set without a date/uid, which breaks readers that
    # require closedAt/closedBy (e.g. the last-closing endpoint -> HTTP 500).
    cr.execute(
        """
        UPDATE account_bank_statement
        SET cash_session_closed_date = COALESCE(
                cash_session_closed_date, write_date),
            cash_session_closed_uid = COALESCE(
                cash_session_closed_uid, write_uid)
        WHERE cash_session_closed = TRUE
          AND (cash_session_closed_date IS NULL
               OR cash_session_closed_uid IS NULL)
        """
    )
    repaired = cr.rowcount
    if marked or repaired:
        _logger.info(
            "Cash sessions backfill: %d marked closed, %d repaired metadata.",
            marked,
            repaired,
        )
