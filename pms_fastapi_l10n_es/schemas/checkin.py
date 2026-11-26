from pydantic import Field

from odoo.addons.pms_fastapi.schemas import checkin


class CheckinDocumentData(checkin.CheckinDocumentData, extends=True):
    documentSupportNumber: str | None = None


class CheckinPartnerDetail(checkin.CheckinPartnerDetail, extends=True):
    relationship: str | None = Field(
        None, description="Relationship from the responsible adult's perspective."
    )


class CheckinPartnerUpdate(checkin.CheckinPartnerUpdate, extends=True):
    documentSupportNumber: str | None = None
    relationship: str | None = Field(
        None, description="Relationship from the responsible adult's perspective."
    )


class CheckinContext(checkin.CheckinContext, extends=True):
    unaccompaniedMinors: bool = False
