from datetime import datetime

from pydantic import Field

from .base import CurrencyAmount, PmsBaseModel
from .currency import CurrencySummary
from .journal import JournalSummary
from .user import UserId


class CashSessionOpenInput(PmsBaseModel):
    journalId: int = Field(description="Cash journal to open the session on.")
    baseAmount: CurrencyAmount = Field(
        description="Declared opening cash balance. 0 is valid."
    )


class CashSessionCloseInput(PmsBaseModel):
    countedCash: CurrencyAmount = Field(
        description="Physically counted cash at close. 0 is valid."
    )
    note: str = Field("", description="Note left for the next shift.")


def _currency_of(statement):
    return statement.currency_id or statement.company_id.currency_id


class CashSessionSummary(PmsBaseModel):
    id: int
    journal: JournalSummary
    openedBy: UserId
    openedAt: datetime
    baseAmount: CurrencyAmount = 0.0
    incomeAmount: CurrencyAmount = 0.0
    refundAmount: CurrencyAmount = 0.0
    expenseAmount: CurrencyAmount = 0.0
    internalTransferAmount: CurrencyAmount = 0.0
    expectedAmount: CurrencyAmount = 0.0
    currency: CurrencySummary

    @classmethod
    def from_account_bank_statement(cls, statement):
        breakdown = statement._pms_cash_session_breakdown()
        currency = _currency_of(statement)
        return cls(
            _decimal_places=currency.decimal_places,
            id=statement.id,
            journal=JournalSummary.from_account_journal(statement.journal_id),
            openedBy=UserId.from_res_users(statement.create_uid),
            openedAt=statement.create_date,
            baseAmount=statement.balance_start,
            incomeAmount=breakdown["income"],
            refundAmount=breakdown["refund"],
            expenseAmount=breakdown["expense"],
            internalTransferAmount=breakdown["internal_transfer"],
            expectedAmount=breakdown["expected"],
            currency=CurrencySummary.from_res_currency(currency),
        )


class CashClosing(PmsBaseModel):
    id: int
    journal: JournalSummary
    openedBy: UserId
    openedAt: datetime
    closedBy: UserId
    closedAt: datetime
    baseAmount: CurrencyAmount = 0.0
    incomeAmount: CurrencyAmount = 0.0
    refundAmount: CurrencyAmount = 0.0
    expenseAmount: CurrencyAmount = 0.0
    internalTransferAmount: CurrencyAmount = 0.0
    countedCash: CurrencyAmount = 0.0
    expectedAmount: CurrencyAmount = 0.0
    difference: CurrencyAmount = 0.0
    closingAmount: CurrencyAmount = 0.0
    note: str = ""
    currency: CurrencySummary

    @classmethod
    def from_account_bank_statement(cls, statement):
        breakdown = statement._pms_cash_session_breakdown()
        currency = _currency_of(statement)
        counted_cash = statement.balance_end_real
        return cls(
            _decimal_places=currency.decimal_places,
            id=statement.id,
            journal=JournalSummary.from_account_journal(statement.journal_id),
            openedBy=UserId.from_res_users(statement.create_uid),
            openedAt=statement.create_date,
            closedBy=UserId.from_res_users(statement.cash_session_closed_uid),
            closedAt=statement.cash_session_closed_date,
            baseAmount=statement.balance_start,
            incomeAmount=breakdown["income"],
            refundAmount=breakdown["refund"],
            expenseAmount=breakdown["expense"],
            internalTransferAmount=breakdown["internal_transfer"],
            countedCash=counted_cash,
            expectedAmount=breakdown["expected"],
            difference=counted_cash - breakdown["expected"],
            closingAmount=counted_cash,
            note=statement.cash_session_note or "",
            currency=CurrencySummary.from_res_currency(currency),
        )


class CashLastClosing(PmsBaseModel):
    closedBy: UserId
    closedAt: datetime
    closingAmount: CurrencyAmount = 0.0
    note: str = ""
    currency: CurrencySummary

    @classmethod
    def from_account_bank_statement(cls, statement):
        currency = _currency_of(statement)
        return cls(
            _decimal_places=currency.decimal_places,
            closedBy=UserId.from_res_users(statement.cash_session_closed_uid),
            closedAt=statement.cash_session_closed_date,
            closingAmount=statement.balance_end_real,
            note=statement.cash_session_note or "",
            currency=CurrencySummary.from_res_currency(currency),
        )
