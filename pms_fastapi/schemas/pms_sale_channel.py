from .base import PmsBaseModel


class SaleChannelSummary(PmsBaseModel):
    id: int
    name: str
    type: str

    @classmethod
    def from_pms_sale_channel(cls, channel):
        return cls(
            id=channel.id,
            name=channel.name,
            type=channel.type,
        )


class SaleChannelId(PmsBaseModel):
    id: int
    name: str

    @classmethod
    def from_pms_sale_channel(cls, channel):
        return cls(
            id=channel.id,
            name=channel.name,
        )
