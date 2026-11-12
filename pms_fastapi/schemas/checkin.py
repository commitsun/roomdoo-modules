from datetime import date
from enum import Enum

from pydantic import Field

from odoo.addons.pms_fastapi.schemas.base import PmsBaseModel


class CheckinGender(str, Enum):
    male = "male"
    female = "female"
    other = "other"


class CheckinGuest(PmsBaseModel):
    id: str
    state: str = Field(
        description="Raw state of the check-in partner: dummy, draft, "
        "precheckin, onboard, done or cancel."
    )
    displayName: str | None = None
    birthdate: date | None = None


class CheckinRoom(PmsBaseModel):
    id: str
    name: str
    checkinPartners: list[CheckinGuest] = Field(default_factory=list)


class CheckinGuests(PmsBaseModel):
    rooms: list[CheckinRoom] = Field(default_factory=list)


class CheckinDocumentData(PmsBaseModel):
    """Guest data that a scanned identity document can provide."""

    firstname: str | None = None
    lastname: str | None = None
    lastname2: str | None = None
    gender: CheckinGender | None = None
    birthdate: date | None = None
    nationality: str | None = Field(None, description="ISO 3166-1 alpha-2 code.")
    documentType: str | None = Field(None, description="Identity document type code.")
    documentNumber: str | None = None
    documentCountryId: str | None = Field(
        None, description="ISO 3166-1 alpha-2 code of the document country."
    )
    documentSupportNumber: str | None = None
    countryId: str | None = Field(
        None, description="ISO 3166-1 alpha-2 code of the residence country."
    )
    countryState: str | None = Field(None, description="Residence state name.")
    residenceCity: str | None = None
    residenceStreet: str | None = None
    zip: str | None = None


class CheckinPartnerDetail(CheckinDocumentData):
    """Stored guest data. Empty fields are omitted from the response."""

    responsibleCheckinPartnerId: str | None = None
    relationship: str | None = Field(
        None, description="Relationship from the responsible adult's perspective."
    )
    email: str | None = None
    mobile: str | None = None


class CheckinContext(PmsBaseModel):
    localization: str | None = Field(
        None, description="Lowercase ISO code of the property country."
    )
    hotelName: str
    arrivalDate: date | None = None
    unaccompaniedMinors: bool
    ocrAvailable: bool
    brandName: str | None = None
