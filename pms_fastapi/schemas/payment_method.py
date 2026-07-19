from enum import Enum

from pydantic import Field

from .base import PmsBaseModel


class PaymentMethodTypeEnum(str, Enum):
    inbound = "inbound"
    outbound = "outbound"


class PaymentMethodSummary(PmsBaseModel):
    id: int
    name: str = Field(alias="name")
    type: PaymentMethodTypeEnum = Field(alias="type")

    @classmethod
    def from_account_payment_method_line(cls, account_payment_method_line):
        line = account_payment_method_line
        name = f"{line.journal_id.name} - {line.name}"
        data = {"id": line.id, "name": name, "type": line.payment_type}
        return cls(**data)
