from pydantic import Field

from odoo.addons.pms_fastapi.schemas.base import PmsBaseModel

# Decimals res.partner keeps for a coordinate (about 1 cm). Rounding to them
# drops the float noise of the stored value (-8.395999999999999).
COORDINATE_DIGITS = 7


class PortalAddress(PmsBaseModel):
    street: str
    postalCode: str
    city: str
    province: str
    country: str

    @classmethod
    def from_res_partner(cls, partner):
        return cls(
            street=partner.street or "",
            postalCode=partner.zip or "",
            city=partner.city or "",
            province=partner.state_id.name or "",
            country=partner.country_id.name or "",
        )


class PortalProperty(PmsBaseModel):
    id: int
    name: str
    address: PortalAddress
    latitude: float | None
    longitude: float | None
    phone: str
    email: str
    logo: str | None
    images: list[str] = Field(default_factory=list)
    defaultArrivalHour: str
    defaultDepartureHour: str
    timezone: str

    @classmethod
    def _image_url(cls, pms_property, field):
        url = cls.url_image_pms_api_rest(
            pms_property.env, "pms.property", pms_property.id, field
        )
        return url or None

    @staticmethod
    def _coordinate(value, located):
        return round(value, COORDINATE_DIGITS) if located else None

    @classmethod
    def from_pms_property(cls, pms_property):
        # Odoo stores 0.0 for a partner that was never geolocated: that is no
        # location, not a point in the Gulf of Guinea.
        located = bool(pms_property.partner_latitude or pms_property.partner_longitude)
        # There is no gallery yet: the hotel image is its only picture.
        hotel_image = cls._image_url(pms_property, "hotel_image_pms_api_rest")
        return cls(
            id=pms_property.id,
            name=pms_property.name,
            address=PortalAddress.from_res_partner(pms_property.partner_id),
            latitude=cls._coordinate(pms_property.partner_latitude, located),
            longitude=cls._coordinate(pms_property.partner_longitude, located),
            phone=pms_property.phone or "",
            email=pms_property.email or "",
            logo=cls._image_url(pms_property, "logo"),
            images=[hotel_image] if hotel_image else [],
            defaultArrivalHour=pms_property.default_arrival_hour,
            defaultDepartureHour=pms_property.default_departure_hour,
            timezone=pms_property.tz,
        )
