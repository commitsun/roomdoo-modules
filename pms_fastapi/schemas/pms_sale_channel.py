from pydantic import AnyHttpUrl

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
            type=channel.channel_type,
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


class SaleChannelDetail(SaleChannelId):
    image: AnyHttpUrl | None = None

    @classmethod
    def from_pms_sale_channel(cls, channel):
        res = super().from_pms_sale_channel(channel)
        if channel.icon:
            image_url = cls.url_image_pms_api_rest(
                channel.env,
                "pms.sale.channel",
                channel.id,
                "icon",
            )
            if image_url:
                res.image = image_url
        return res
