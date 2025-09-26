from .base import PmsBaseModel


class PricelistId(PmsBaseModel):
    id: int
    name: str

    @classmethod
    def from_product_pricelist(cls, pricelist):
        return cls(
            **{
                "id": pricelist.id,
                "name": pricelist.name,
            }
        )
