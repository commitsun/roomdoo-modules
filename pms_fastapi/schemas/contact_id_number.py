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
    name: str
    category: int
    supportNumber: str
    country: int

    def to_res_partner_id_number(self, partner_id: int) -> dict:
        values = self.model_dump(exclude_unset=True)
        vals = {
            "name": values.get("name"),
            "category_id": values.get("category"),
            "support_number": values.get("supportNumber"),
            "country_id": values.get("country"),
            "partner_id": partner_id,
        }
        return vals


class ContactIdNumberUpdate(PmsBaseModel):
    name: str = ""
    category: int = 0
    supportNumber: str = ""
    country: int = 0

    def to_res_partner_id_number(self) -> dict:
        values = self.model_dump(exclude_unset=True)
        vals = {}
        if "name" in values:
            vals["name"] = values.get("name")
        if "category" in values:
            vals["category_id"] = values.get("category")
        if "supportNumber" in values:
            vals["support_number"] = values.get("supportNumber")
        if "country" in values:
            vals["country_id"] = values.get("country")
        return vals


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
