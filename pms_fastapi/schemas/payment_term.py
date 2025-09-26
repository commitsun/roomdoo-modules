from .base import PmsBaseModel


class PaymentTermId(PmsBaseModel):
    id: int
    name: str

    @classmethod
    def from_account_payment_term(cls, payment_term):
        return cls(
            **{
                "id": payment_term.id,
                "name": payment_term.name,
            }
        )
