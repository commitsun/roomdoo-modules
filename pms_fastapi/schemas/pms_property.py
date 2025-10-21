from pydantic import AnyHttpUrl

from .base import PmsBaseModel
from .currency import CurrencySummary


class PropertyId(PmsBaseModel):
    id: int
    name: str

    @classmethod
    def parse_common_fields(cls, pms_property) -> dict:
        record_dict = {
            "id": pms_property.id,
            "name": pms_property.name,
        }
        return record_dict

    @classmethod
    def from_pms_property(cls, pms_property):
        data = cls.parse_common_fields(pms_property)
        return cls(**data)


class PropertySummary(PropertyId):
    image: AnyHttpUrl | None = None
    currency: CurrencySummary

    @classmethod
    def from_pms_property(cls, pms_property):
        data = cls.parse_common_fields(pms_property)
        data["currency"] = CurrencySummary.from_res_currency(
            pms_property.company_id.currency_id
        )
        image_url = cls.url_image_pms_api_rest(
            pms_property.env,
            "pms.property",
            pms_property.id,
            "hotel_image_pms_api_rest",
        )
        if image_url:
            data["image"] = image_url
        return cls(**data)
