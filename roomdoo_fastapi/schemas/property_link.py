from odoo.addons.pms_fastapi.schemas.base import PmsBaseModel


class PropertyLink(PmsBaseModel):
    id: int
    label: str
    isSupportLink: bool

    @classmethod
    def from_pms_property_menu(cls, menu):
        return PropertyLink(id=menu.id, label=menu.name, isSupportLink=menu.support_url)
