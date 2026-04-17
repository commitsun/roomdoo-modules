from pydantic import Field

from .base import PmsBaseModel


class PaymentMethodSummary(PmsBaseModel):
    id: int
    name: str = Field(alias="name")

    @classmethod
    def from_account_payment_method_line(cls, account_payment_method_line):
        line = account_payment_method_line
        name = f"{line.journal_id.name} - {line.name}"
        data = {"id": line.id, "name": name}
        return cls(**data)
