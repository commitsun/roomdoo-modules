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
    image: AnyHttpUrl = ""
    defaultProperty: PropertyId

    @classmethod
    def from_res_users(cls, odoo_record):
        user = cls(
            id=odoo_record.id,
            name=odoo_record.name,
            firstName=odoo_record.firstname or "",
            lastName=odoo_record.lastname or "",
            lastName2=odoo_record.lastname2 or "",
            email=odoo_record.email or "",
            phone=odoo_record.phone or "",
            defaultProperty=PropertyId(
                id=odoo_record.pms_property_id.id, name=odoo_record.pms_property_id.name
            ),
        )
        image_url = cls.url_image_pms_api_rest(
            "res.partner", odoo_record.partner_id.id, "image_1024"
        )
        if image_url:
            user.image = image_url
        return user
