from pydantic import Field

from .base import PmsBaseModel


class JournalSummary(PmsBaseModel):
    id: int
    name: str = Field(alias="name")

    @classmethod
    def from_account_journal(cls, account_journal):
        return cls(id=account_journal.id, name=account_journal.name)
