from pydantic import AnyHttpUrl

from .base import PmsBaseModel
from .pms_property import PropertyId


class User(PmsBaseModel):
    id: int
    name: str
    firstname: str = ""
    lastname: str = ""
    email: str = ""
    phone: str = ""
    image: AnyHttpUrl | None = None
    defaultPmsProperty: PropertyId | None = None

    @classmethod
    def from_res_users(cls, user_record):
        data = {
            "id": user_record.id,
            "name": user_record.name,
            "firstname": user_record.firstname or "",
            "lastname": user_record.lastname or "",
            "email": user_record.email or "",
            "phone": user_record.phone or "",
        }
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
