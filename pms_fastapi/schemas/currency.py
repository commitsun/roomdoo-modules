from pydantic import Field

from .base import PmsBaseModel


class CurrencyId(PmsBaseModel):
    id: int
    name: str

    @classmethod
    def parse_common_fields(cls, currency) -> dict:
        return {
            "id": currency.id,
            "name": currency.full_name,
        }

    @classmethod
    def from_res_currency(cls, currency):
        data = cls.parse_common_fields(currency)
        return cls(**data)


class CurrencySummary(CurrencyId):
    code: str = Field(description="ISO 4217 currency code")

    @classmethod
    def from_res_currency(cls, currency):
        data = cls.parse_common_fields(currency)
        data["code"] = currency.name
        return cls(**data)
