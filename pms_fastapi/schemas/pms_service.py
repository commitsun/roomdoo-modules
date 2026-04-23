from pydantic import Field

from .base import PmsBaseModel


class ServiceId(PmsBaseModel):
    id: int
    name: str

    @classmethod
    def from_pms_service(cls, service):
        filtered_data = cls._read_odoo_record(service)
        return cls(**filtered_data)


class ServiceProduct(PmsBaseModel):
    id: int
    name: str
    is_board_service: bool = Field(False, alias="isBoardService")
    per_day: bool = Field(False, alias="perDay")
    per_person: bool = Field(False, alias="perPerson")

    @classmethod
    def from_product_product(cls, product, is_board_service: bool = False):
        return cls(
            id=product.id,
            name=product.name,
            is_board_service=is_board_service,
            per_day=product.per_day,
            per_person=product.per_person,
        )
