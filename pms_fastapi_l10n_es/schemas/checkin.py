from pydantic import Field

from odoo.addons.pms_fastapi.schemas import checkin


class CheckinDocumentData(checkin.CheckinDocumentData, extends=True):
    documentSupportNumber: str | None = None


class CheckinPartnerDetail(checkin.CheckinPartnerDetail, extends=True):
    responsibleCheckinPartnerId: int | None = None
    relationship: str | None = Field(
        None, description="Relationship from the responsible adult's perspective."
    )


class CheckinPartnerUpdate(checkin.CheckinPartnerUpdate, extends=True):
    documentSupportNumber: str | None = None
    responsibleCheckinPartnerId: int | None = Field(
        None, description="Check-in partner of the same folio that is responsible."
    )
    relationship: str | None = Field(
        None, description="Relationship from the responsible adult's perspective."
    )


class CheckinContext(checkin.CheckinContext, extends=True):
    unaccompaniedMinors: bool = False
