"""Close cash sessions left open by the 16.0.1.4.0 backfill.

The 16.0.1.4.0 backfill marked ``cash_session_closed = TRUE`` only where the
standard accounting flag ``is_complete`` was ``TRUE``. That premise was
incomplete: ``is_complete`` is a computed *stored* field that was never
recomputed for tens of thousands of historical statements, so it sits at
``NULL`` (neither ``TRUE`` nor ``FALSE``) on them. ``NULL = TRUE`` is false, so
those statements kept ``cash_session_closed = NULL``.

Because the ORM reads ``cash_session_closed = False`` as matching ``NULL`` too,
and ``get_current`` returns the newest such statement per journal without
checking for a *later* closed one, every cash journal that had one of these
stale statements (typically empty ones, left behind a session that was in fact
closed afterwards) wrongly reported an open cash session.

We close any cash statement that is superseded by a later closed statement in
the same journal, using the same ``create_date`` ordering ``get_current`` uses
to pick the "current" session. A statement with no later close is left open, so
the genuinely-current shift per cash journal stays open. Closing metadata is
approximated from the write metadata, the same heuristic 16.0.1.4.0 used.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
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
          AND st.cash_session_closed IS NOT TRUE
          AND EXISTS (
              SELECT 1
              FROM account_bank_statement later
              WHERE later.journal_id = st.journal_id
                AND later.cash_session_closed = TRUE
                AND (later.create_date > st.create_date
                     OR (later.create_date = st.create_date
                         AND later.id > st.id))
          )
        """
    )
    closed = cr.rowcount
    if closed:
        _logger.info("Cash sessions backfill fix: %d stale sessions closed.", closed)
