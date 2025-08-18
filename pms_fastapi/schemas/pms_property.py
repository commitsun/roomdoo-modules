from .base import PmsBaseModel


class PropertyId(PmsBaseModel):
    id: int
    name: str

    @classmethod
    def from_pms_property(cls, pms_property):
        data = {"id": pms_property.id, "name": pms_property.name}
        return cls(**data)


class PropertySummary(PropertyId):
    pass  # temporary empty
