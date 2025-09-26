from .base import PmsBaseModel


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
