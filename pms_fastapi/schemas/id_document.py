from odoo.addons.pms_fastapi.schemas.base import PmsBaseModel


class IdDocument(PmsBaseModel):
    type: str
    number: str

    @classmethod
    def from_id_number(cls, document_rec):
        return cls(type=document_rec.category_id.name, number=document_rec.name)

    @classmethod
    def from_pms_checkin_partner(cls, checkin_partner):
        return cls(
            type=checkin_partner.document_type.name,
            number=checkin_partner.document_number,
        )
