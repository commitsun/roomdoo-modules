from datetime import date
from enum import Enum

from pydantic import Field

from odoo.addons.pms_fastapi.schemas.base import PmsBaseModel
from odoo.addons.pms_fastapi.schemas.country import CountrySummary
from odoo.addons.pms_fastapi.schemas.id_document import IdDocument


class CheckinStateEnum(str, Enum):
    pending = "pending"
    partial = "partial"
    complete = "complete"


# Mapping from Odoo pms.checkin.partner state to API checkinStateEnum
ODOO_CHECKIN_STATE_MAP = {
    "dummy": CheckinStateEnum.pending,
    "draft": CheckinStateEnum.partial,
    "precheckin": CheckinStateEnum.complete,
    "onboard": CheckinStateEnum.complete,
    "done": CheckinStateEnum.complete,
    "cancel": CheckinStateEnum.pending,
}


class ReservationGuest(PmsBaseModel):
    id: int
    name: str = ""
    checkinState: CheckinStateEnum
    nationality: CountrySummary | None = None
    birthdate_date: date | None = Field(None, alias="birthdate")
    identificationDocument: IdDocument | None = None

    @classmethod
    def from_pms_checkin_partner(cls, checkin_partner):
        data = cls._read_odoo_record(checkin_partner)
        data["checkinState"] = ODOO_CHECKIN_STATE_MAP.get(
            checkin_partner.state, CheckinStateEnum.pending
        )
        if checkin_partner.nationality_id:
            data["nationality"] = CountrySummary.from_res_country(
                checkin_partner.nationality_id
            )
        if checkin_partner.document_number and checkin_partner.document_type:
            data["identificationDocument"] = IdDocument.from_pms_checkin_partner(
                checkin_partner
            )
        return cls(**data)
