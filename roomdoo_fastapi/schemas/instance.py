from pydantic import AnyHttpUrl

from odoo.addons.pms_fastapi.schemas.base import PmsBaseModel


class Instance(PmsBaseModel):
    name: str
    image: AnyHttpUrl
