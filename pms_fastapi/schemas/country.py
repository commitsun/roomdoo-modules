from .base import PmsBaseModel


class CountryId(PmsBaseModel):
    id: int
    name: str

    @classmethod
    def from_res_country(cls, country):
        return cls(id=country.id, name=country.name)
