from odoo.addons.pms_fastapi.schemas.base import PmsBaseModel


class PropertyLink(PmsBaseModel):
    id: int
    label: str
    support_link: bool

    @classmethod
    def from_pms_property_menu(cls, menu):
        return PropertyLink(id=menu.id, label=menu.name, support_link=menu.support_url)
