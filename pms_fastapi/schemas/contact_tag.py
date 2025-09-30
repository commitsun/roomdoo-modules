from .base import PmsBaseModel


class ContactTagId(PmsBaseModel):
    id: int
    name: str

    @classmethod
    def from_res_partner_category(cls, partner_category):
        return cls(
            **{
                "id": partner_category.id,
                "name": partner_category.name,
            }
        )
