from pydantic import AnyHttpUrl, Field

from .base import PmsBaseModel
from .pms_property import PropertyId


class UserId(PmsBaseModel):
    id: int
    name: str = Field(alias="name")

    @classmethod
    def from_res_users(cls, user):
        return cls(id=user.id, name=user.name or "")


class User(PmsBaseModel):
    id: int = Field(alias="id")
    name: str = Field(alias="name")
    firstname: str = Field("", alias="firstname")
    lastname: str = Field("", alias="lastname")
    login: str = Field("", alias="login")
    email: str = Field("", alias="email")
    phone: str = Field("", alias="phone")
    lang: str = Field("", alias="lang")
    image: AnyHttpUrl | None = None
    defaultPmsProperty: PropertyId | None = None

    @classmethod
    def from_res_users(cls, user_record):
        record = user_record.read()[0]
        model_fields = cls.model_fields.keys()
        data = {k: v for k, v in record.items() if v and k in model_fields}
        if user_record.pms_property_id:
            data["defaultPmsProperty"] = PropertyId.from_pms_property(
                user_record.pms_property_id
            )
        image_url = cls.url_image_pms_api_rest(
            user_record.env, "res.partner", user_record.partner_id.id, "image_1024"
        )
        if image_url:
            data["image"] = image_url
        return cls(**data)


class UserUpdate(PmsBaseModel):
    firstname: str | None = Field(None, alias="firstname")
    lastname: str | None = Field(None, alias="lastname")
    email: str | None = Field(None, alias="email")
    login: str = Field("", alias="login")
    phone: str | None = Field(None, alias="phone")
    lang: str | None = Field(None, alias="lang")

    def to_res_users(self) -> dict:
        data = self.model_dump(exclude_unset=True)
        return data
