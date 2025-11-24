from odoo.addons.pms_fastapi.schemas.base import PmsBaseModel


class IdDocument(PmsBaseModel):
    type: str
    number: str

    @classmethod
    def from_id_number(cls, document_rec):
        return IdDocument(type=document_rec.category_id.name, number=document_rec.name)
