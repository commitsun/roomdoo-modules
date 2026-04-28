from pydantic import Field

from odoo.addons.pms_bookai.schemas.bookai_template import BookaiBaseModel


class BookaiPropertyConfig(BookaiBaseModel):
    id: int
    name: str
    externalCode: str = ""
    bookaiMode: str = "disabled"
    bookaiOnlineSelling: bool = False
    bookaiAppUrl: str = ""
    hasWhatsapp: bool = False

    @classmethod
    def from_pms_property(cls, pms_property, fallback_app_url=""):
        fallback_code = ""
        if "pms_property_code" in pms_property._fields:
            fallback_code = pms_property.pms_property_code or ""
        return cls(
            id=pms_property.id,
            name=pms_property.name or "",
            externalCode=pms_property.external_code or fallback_code,
            bookaiMode=pms_property.bookai_mode or "disabled",
            bookaiOnlineSelling=pms_property.bookai_online_selling,
            bookaiAppUrl=pms_property.bookai_app_url or fallback_app_url,
            hasWhatsapp=bool(pms_property.bookai_wa_phone_id),
        )


class BookaiConfig(BookaiBaseModel):
    bookaiEnabled: bool = False
    bookaiBaseUrl: str = ""
    bookaiToken: str = ""
    properties: list[BookaiPropertyConfig] = Field(default_factory=list)
