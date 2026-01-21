from pydantic import Field

from .base import PmsBaseModel


class PaymentMethodSummary(PmsBaseModel):
    id: int
    name: str = Field(alias="name")

    @classmethod
    def from_account_payment_method(cls, account_payment_method):
        record = account_payment_method.read()[0]
        model_fields = cls.model_fields.keys()
        data = {k: v for k, v in record.items() if v and k in model_fields}
        return cls(**data)

    @classmethod
    def from_account_payment_method_line(cls, account_payment_method_line):
        return cls.from_account_payment_method(
            account_payment_method_line.payment_method_id
        )
