from pydantic import Field

from .base import PmsBaseModel
from .country import CountryId


class ContactIdNumberCategoryId(PmsBaseModel):
    id: int
    name: str

    @classmethod
    def from_res_partner_id_number_category(cls, id_number_category):
        return cls(
            **{
                "id": id_number_category.id,
                "name": id_number_category.name,
            }
        )


class ContactIdNumberCategorySummary(PmsBaseModel):
    id: int
    name: str
    code: str
    countries: list[CountryId]

    @classmethod
    def from_res_partner_id_number_category(cls, id_number_category):
        res = {
            "id": id_number_category.id,
            "name": id_number_category.name,
            "code": id_number_category.code,
            "countries": [],
        }
        for country in id_number_category.country_ids:
            res["countries"].append(CountryId.from_res_country(country))
        return cls(**res)


class ContactIdNumberId(PmsBaseModel):
    id: int
    name: str

    @classmethod
    def from_res_partner_id_number(cls, id_number):
        return cls(
            **{
                "id": id_number.id,
                "name": id_number.name,
            }
        )


class ContactIdNumberInsert(PmsBaseModel):
    name: str = Field(alias="name")
    category_id: int = Field(alias="category")
    support_number: str = Field(alias="supportNumber")
    country_id: int = Field(alias="country")

    def to_res_partner_id_number(self, partner_id: int = 0) -> dict:
        data = self.dict(
            exclude_unset=True,
        )
        if partner_id:
            data["partner_id"] = partner_id
        return data


class ContactIdNumberUpdate(ContactIdNumberInsert):
    name: str = Field("", alias="name")
    category_id: int = Field(0, alias="category")
    support_number: str = Field("", alias="supportNumber")
    country_id: int = Field(0, alias="country")


class ContactIdNumberSummary(PmsBaseModel):
    id: int
    category: ContactIdNumberCategoryId | None = None
    name: str = ""
    supportNumber: str = ""
    country: CountryId | None = None

    @classmethod
    def from_res_partner_id_number(cls, id_number):
        res = {
            "id": id_number.id,
            "name": id_number.name or "",
            "supportNumber": id_number.support_number or "",
        }
        category = None
        if id_number.category_id:
            category = ContactIdNumberCategoryId.from_res_partner_id_number_category(
                id_number.category_id
            )
        res["category"] = category
        country = None
        if id_number.country_id:
            country = CountryId.from_res_country(id_number.country_id)
        res["country"] = country
        return cls(**res)
