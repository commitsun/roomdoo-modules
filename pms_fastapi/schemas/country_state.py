from .base import PmsBaseModel
from .country import CountryId


class CountryStateId(PmsBaseModel):
    id: int
    name: str

    @classmethod
    def from_res_country_state(cls, state):
        return cls(
            **{
                "id": state.id,
                "name": state.name,
            }
        )


class CountryStateSummary(PmsBaseModel):
    id: int
    name: str
    country: CountryId

    @classmethod
    def from_res_country_state(cls, state):
        return cls(
            **{
                "id": state.id,
                "name": state.name,
                "country": CountryId.from_res_country(state.country_id),
            }
        )
