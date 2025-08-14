from pydantic import AnyHttpUrl

from .base import PmsBaseModel
from .pms_property import PropertyId


class User(PmsBaseModel):
    id: int
    name: str
    firstName: str = ""
    lastName: str = ""
    lastName2: str = ""
    email: str = ""
    phone: str = ""
    image: AnyHttpUrl | None = None
    defaultProperty: PropertyId | None = None

    @classmethod
    def from_res_users(cls, user_record):
        data = {
            "id": user_record.id,
            "name": user_record.name,
            "firstName": user_record.firstname or "",
            "lastName": user_record.lastname or "",
            "lastName2": user_record.lastname2 or "",
            "email": user_record.email or "",
            "phone": user_record.phone or "",
        }
        if user_record.pms_property_id:
            data["defaultProperty"] = PropertyId(
                id=user_record.pms_property_id.id, name=user_record.pms_property_id.name
            )
        image_url = cls.url_image_pms_api_rest(
            user_record.env, "res.partner", user_record.partner_id.id, "image_1024"
        )
        if image_url:
            data["image"] = image_url
        return cls(**data)
