from .base import PmsBaseModel


class CountryId(PmsBaseModel):
    id: int
    name: str

    @classmethod
    def parse_common_fields(cls, country) -> dict:
        return {
            "id": country.id,
            "name": country.name,
        }

    @classmethod
    def from_res_country(cls, country):
        data = cls.parse_common_fields(country)
        return cls(**data)


class CountrySummary(CountryId):
    code: str

    @classmethod
    def from_res_country(cls, country):
        data = cls.parse_common_fields(country)
        data["code"] = country.code
        return cls(**data)
