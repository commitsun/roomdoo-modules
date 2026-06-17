from fastapi import Response, status
from fastapi.responses import JSONResponse

from odoo import _, models

from odoo.addons.pms_fastapi.dependencies import AuthenticatedEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router
from odoo.addons.pms_fastapi.schemas.base import PmsBaseModel
from odoo.addons.pms_fastapi.schemas.cash_session import (
    CashClosing,
    CashLastClosing,
    CashSessionCloseInput,
    CashSessionOpenInput,
    CashSessionSummary,
)


class _CashSessionProblem(Exception):
    """Control-flow exception carrying an RFC 9457 JSONResponse."""

    def __init__(self, response):
        super().__init__()
        self.response = response


@pms_api_router.post(
    "/cash-sessions",
    response_model=CashSessionSummary,
    status_code=201,
    tags=["account"],
)
async def open_cash_session(
    env: AuthenticatedEnv,
    payload: CashSessionOpenInput,
) -> CashSessionSummary:
    """Open a cash session (shift) on a cash journal with an initial balance.

    Only one open session is allowed per journal.
    """
    return env["pms_api_cash_session.cash_session_router.helper"].new().open(payload)


@pms_api_router.get(
    "/cash-sessions/current",
    response_model=CashSessionSummary,
    responses={204: {"description": "No open cash session on the journal"}},
    tags=["account"],
)
async def get_current_cash_session(
    env: AuthenticatedEnv,
    journalId: int,
) -> CashSessionSummary | Response:
    """Return the open cash session of the journal with its financial
    breakdown. Responds 204 when there is no open session."""
    return (
        env["pms_api_cash_session.cash_session_router.helper"]
        .new()
        .get_current(journalId)
    )


@pms_api_router.get(
    "/cash-sessions/last-closing",
    response_model=CashLastClosing,
    responses={204: {"description": "The journal never had a cash session close"}},
    tags=["account"],
)
async def get_last_cash_closing(
    env: AuthenticatedEnv,
    journalId: int,
) -> CashLastClosing | Response:
    """Return the last cash session close of the journal. Responds 204 when
    the journal never had a close."""
    return (
        env["pms_api_cash_session.cash_session_router.helper"]
        .new()
        .get_last_closing(journalId)
    )


@pms_api_router.post(
    "/cash-sessions/{session_id}/closing",
    response_model=CashClosing,
    tags=["account"],
)
async def close_cash_session(
    env: AuthenticatedEnv,
    session_id: int,
    payload: CashSessionCloseInput,
) -> CashClosing:
    """Close the cash session recording the physically counted cash and an
    optional note. The backend recomputes the mismatch and closing balance.
    The mismatch is recorded but never blocks the close."""
    return (
        env["pms_api_cash_session.cash_session_router.helper"]
        .new()
        .close(session_id, payload)
    )


class PmsApiCashSessionRouterHelper(models.AbstractModel):
    _name = "pms_api_cash_session.cash_session_router.helper"
    _description = "PMS API Cash Session Router Helper"

    @staticmethod
    def _problem(status_code, type_, title, detail):
        raise _CashSessionProblem(
            JSONResponse(
                status_code=status_code,
                content={
                    "type": type_,
                    "title": title,
                    "status": status_code,
                    "detail": detail,
                },
                media_type="application/problem+json",
            )
        )

    def _resolve_cash_journal(self, journal_id):
        journal = self.env["account.journal"].sudo().browse(journal_id).exists()
        if not journal or journal.type != "cash":
            self._problem(
                409,
                "/errors/journal-not-cash",
                _("Journal is not a cash journal"),
                _(
                    "Journal %s is not of type cash; cannot operate a "
                    "cash session on it."
                )
                % journal_id,
            )
        PmsBaseModel.pms_api_check_access(self.env.user, journal)
        return journal

    def _open_session(self, journal_id):
        return (
            self.env["account.bank.statement"]
            .sudo()
            .search(
                [
                    ("journal_id", "=", journal_id),
                    ("cash_session_closed", "=", False),
                ],
                order="create_date desc, id desc",
                limit=1,
            )
        )

    def open(self, payload: CashSessionOpenInput):
        try:
            journal = self._resolve_cash_journal(payload.journalId)
            if self._open_session(journal.id):
                self._problem(
                    409,
                    "/errors/cash-session-already-open",
                    _("Cash session already open"),
                    _("Journal %s already has an open cash session.") % journal.id,
                )
            pms_property = journal.pms_property_ids
            pms_property = (
                pms_property
                if len(pms_property) == 1
                else self.env.user.pms_property_id
            )
            statement = (
                self.env["account.bank.statement"]
                .sudo()
                ._pms_create_cash_session(journal, payload.baseAmount, pms_property)
            )
        except _CashSessionProblem as problem:
            return problem.response
        return CashSessionSummary.from_account_bank_statement(statement)

    def get_current(self, journal_id):
        try:
            journal = self._resolve_cash_journal(journal_id)
        except _CashSessionProblem as problem:
            return problem.response
        statement = self._open_session(journal.id)
        if not statement:
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        return CashSessionSummary.from_account_bank_statement(statement)

    def get_last_closing(self, journal_id):
        try:
            journal = self._resolve_cash_journal(journal_id)
        except _CashSessionProblem as problem:
            return problem.response
        statement = (
            self.env["account.bank.statement"]
            .sudo()
            .search(
                [
                    ("journal_id", "=", journal.id),
                    ("cash_session_closed", "=", True),
                ],
                order="cash_session_closed_date desc, id desc",
                limit=1,
            )
        )
        if not statement:
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        return CashLastClosing.from_account_bank_statement(statement)

    def close(self, session_id, payload: CashSessionCloseInput):
        try:
            statement = (
                self.env["account.bank.statement"].sudo().browse(session_id).exists()
            )
            if not statement or statement.journal_id.type != "cash":
                self._problem(
                    404,
                    "/errors/cash-session-not-found",
                    _("Cash session not found"),
                    _("Cash session %s does not exist.") % session_id,
                )
            if statement.cash_session_closed:
                self._problem(
                    409,
                    "/errors/cash-session-already-closed",
                    _("Cash session already closed"),
                    _("Cash session %s is already closed.") % session_id,
                )
            PmsBaseModel.pms_api_check_access(self.env.user, statement)
            statement._pms_close_cash_session(payload.countedCash, payload.note)
        except _CashSessionProblem as problem:
            return problem.response
        return CashClosing.from_account_bank_statement(statement)
